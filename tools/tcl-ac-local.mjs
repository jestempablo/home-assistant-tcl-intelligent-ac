#!/usr/bin/env node

import crypto from "node:crypto";
import dgram from "node:dgram";
import fs from "node:fs";

const IV = Buffer.from("562e17996d093d28ddb3ba695a2e6f58", "hex");
const DEVICE_TYPE = 0x507c;
const COMMAND = 0x006a;
const RESPONSE_COMMAND = 0x03ee;
const DEFAULT_CONFIG = "config/tcl-ac.local.json";

function usage() {
  console.error(
    [
      "Usage:",
      "  node tools/tcl-ac-local.mjs <device> get",
      "  node tools/tcl-ac-local.mjs <device> set <param> <value>",
      "  node tools/tcl-ac-local.mjs --host <ip> --mac <mac> --key <hex> get",
      "",
      "Examples:",
      "  node tools/tcl-ac-local.mjs example get",
      "  node tools/tcl-ac-local.mjs example set temp 230",
      "  node tools/tcl-ac-local.mjs example set pwr 1",
    ].join("\n"),
  );
}

function parseArgs(argv) {
  const options = { config: DEFAULT_CONFIG };
  const positional = [];

  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--config") options.config = argv[++i];
    else if (arg === "--host") options.host = argv[++i];
    else if (arg === "--mac") options.mac = argv[++i];
    else if (arg === "--key") options.key = argv[++i];
    else if (arg === "--timeout") options.timeoutMs = Number(argv[++i]);
    else positional.push(arg);
  }

  if (options.host || options.mac || options.key) {
    const [action, param, rawValue] = positional;
    return { ...options, action, param, rawValue };
  }

  const [deviceName, action, param, rawValue] = positional;
  return { ...options, deviceName, action, param, rawValue };
}

function loadDevice(options) {
  if (options.host && options.mac && options.key) {
    return {
      name: options.host,
      host: options.host,
      mac: options.mac,
      key: options.key,
    };
  }

  if (!options.deviceName) throw new Error("Missing device name.");
  const config = JSON.parse(fs.readFileSync(options.config, "utf8"));
  const device = config.devices?.[options.deviceName];
  if (!device) throw new Error(`Device '${options.deviceName}' not found in ${options.config}.`);
  return { name: options.deviceName, ...device };
}

function parseValue(raw) {
  if (raw === "true") return true;
  if (raw === "false") return false;
  if (raw === "null") return null;
  if (/^-?\d+(\.\d+)?$/.test(raw)) return Number(raw);
  return raw;
}

function checksum(buffer) {
  let value = 0xbeaf;
  for (const byte of buffer) value = (value + byte) & 0xffff;
  return value;
}

function macToReversedBuffer(mac) {
  const compact = mac.replace(/[:-]/g, "").toLowerCase();
  if (!/^[0-9a-f]{12}$/.test(compact)) throw new Error(`Invalid MAC: ${mac}`);
  return Buffer.from(compact, "hex").reverse();
}

function keyToBuffer(key) {
  const compact = key.trim().toLowerCase();
  if (!/^[0-9a-f]{32}$/.test(compact)) throw new Error("Device key must be 16 bytes hex.");
  return Buffer.from(compact, "hex");
}

function encodeInnerPayload(action, body) {
  const bodyBuffer = Buffer.from(JSON.stringify(body), "utf8");
  const meaningfulLength = 12 + bodyBuffer.length;
  const plainLength = Math.ceil((14 + bodyBuffer.length) / 16) * 16;
  const plain = Buffer.alloc(plainLength);

  plain.writeUInt16LE(meaningfulLength, 0);
  Buffer.from("a5a55a5a", "hex").copy(plain, 2);
  plain[8] = action === "get" ? 1 : 2;
  plain[9] = 0x0b;
  plain.writeUInt32LE(bodyBuffer.length, 10);
  bodyBuffer.copy(plain, 14);

  const innerChecksum = checksum(Buffer.concat([plain.slice(2, 6), plain.slice(8)]));
  plain.writeUInt16LE(innerChecksum, 6);
  return plain;
}

function encryptPayload(plain, key) {
  const cipher = crypto.createCipheriv("aes-128-cbc", key, IV);
  cipher.setAutoPadding(false);
  return Buffer.concat([cipher.update(plain), cipher.final()]);
}

function decryptPayload(encrypted, key) {
  const decipher = crypto.createDecipheriv("aes-128-cbc", key, IV);
  decipher.setAutoPadding(false);
  return Buffer.concat([decipher.update(encrypted), decipher.final()]);
}

function buildPacket(device, plainPayload) {
  const key = keyToBuffer(device.key);
  const encrypted = encryptPayload(plainPayload, key);
  const packet = Buffer.alloc(56 + encrypted.length);

  Buffer.from("5aa5aa555aa5aa55", "hex").copy(packet, 0);
  packet.writeUInt16LE(DEVICE_TYPE, 0x24);
  packet.writeUInt16LE(COMMAND, 0x26);
  crypto.randomBytes(2).copy(packet, 0x28);
  macToReversedBuffer(device.mac).copy(packet, 0x2a);
  packet.writeUInt32LE(1, 0x30);
  packet.writeUInt16LE(checksum(plainPayload), 0x34);
  encrypted.copy(packet, 0x38);

  const packetForChecksum = Buffer.from(packet);
  packetForChecksum[0x20] = 0;
  packetForChecksum[0x21] = 0;
  packet.writeUInt16LE(checksum(packetForChecksum), 0x20);

  return packet;
}

function sendUdp(host, packet, timeoutMs = 3000) {
  return new Promise((resolve, reject) => {
    const socket = dgram.createSocket("udp4");
    const timeout = setTimeout(() => {
      socket.close();
      reject(new Error(`Timeout waiting for ${host}:80`));
    }, timeoutMs);

    socket.on("error", (error) => {
      clearTimeout(timeout);
      socket.close();
      reject(error);
    });

    socket.on("message", (message) => {
      clearTimeout(timeout);
      socket.close();
      resolve(message);
    });

    socket.bind(0, "0.0.0.0", () => socket.send(packet, 80, host));
  });
}

function decodeResponse(packet, key) {
  if (packet.length < 72) throw new Error(`Response too short: ${packet.length}`);
  if (packet.readUInt16LE(0x26) !== RESPONSE_COMMAND) {
    throw new Error(`Unexpected response command 0x${packet.readUInt16LE(0x26).toString(16)}`);
  }

  const plain = decryptPayload(packet.slice(0x38), key);
  const expectedPayloadChecksum = packet.readUInt16LE(0x34);
  const actualPayloadChecksum = checksum(plain);
  if (expectedPayloadChecksum !== actualPayloadChecksum) {
    throw new Error(
      `Payload checksum mismatch: got 0x${actualPayloadChecksum.toString(16)}, expected 0x${expectedPayloadChecksum.toString(16)}`,
    );
  }

  const meaningfulLength = plain.readUInt16LE(0);
  const bodyLength = meaningfulLength - 12;
  if (bodyLength < 0 || 14 + bodyLength > plain.length) {
    throw new Error(`Invalid inner payload length: ${meaningfulLength}`);
  }

  const bodyText = plain.slice(14, 14 + bodyLength).toString("utf8");
  return JSON.parse(bodyText);
}

function buildBody(action, param, rawValue) {
  if (action === "get") return {};
  if (action !== "set") throw new Error(`Unsupported action: ${action}`);
  if (!param || rawValue === undefined) throw new Error("Set requires <param> <value>.");
  return { [param]: parseValue(rawValue) };
}

function stateToHaShape(state) {
  return {
    power: state.pwr === 1,
    current_temperature: state.envtemp,
    target_temperature: typeof state.temp === "number" ? state.temp / 10 : state.temp,
    hvac_mode_code: state.tcl_mode,
    fan_mode_code: state.tcl_mark,
    vertical_swing_code: state.tcl_vdir,
    horizontal_swing_code: state.tcl_hdir,
    raw: state,
  };
}

const options = parseArgs(process.argv.slice(2));

try {
  if (!["get", "set"].includes(options.action)) {
    usage();
    process.exit(2);
  }

  const device = loadDevice(options);
  const key = keyToBuffer(device.key);
  const body = buildBody(options.action, options.param, options.rawValue);
  const plain = encodeInnerPayload(options.action, body);
  const packet = buildPacket(device, plain);
  const response = await sendUdp(device.host, packet, options.timeoutMs || 3000);
  const state = decodeResponse(response, key);

  console.log(JSON.stringify(stateToHaShape(state), null, 2));
} catch (error) {
  console.error(error.message);
  process.exit(1);
}
