#!/usr/bin/env node

import crypto from "node:crypto";
import dgram from "node:dgram";

const IV = Buffer.from("562e17996d093d28ddb3ba695a2e6f58", "hex");
const INIT_KEY = Buffer.from("097628343fe99e23765c1513accf8b02", "hex");
const MAGIC = Buffer.from("5aa5aa555aa5aa55", "hex");
const INNER_MAGIC = Buffer.from("a5a55a5a", "hex");
const DEFAULT_PORT = 80;
const DISCOVERY_PAYLOAD = Buffer.from(
  "5aa5aa555aa5aa5502000000ea07041f0a001805000000001002000a688d0000ffc40000000006000000000000000000",
  "hex",
);

function usage() {
  console.error(
    [
      "Usage:",
      "  node tools/broadlink-auth-test.mjs <host> [host...]",
      "",
      "Options:",
      "  --show-key   Print the full auth key instead of a redacted key.",
    ].join("\n"),
  );
}

function parseArgs(argv) {
  const options = { showKey: false, hosts: [] };
  for (const arg of argv) {
    if (arg === "--show-key") options.showKey = true;
    else if (arg === "-h" || arg === "--help") options.help = true;
    else options.hosts.push(arg);
  }
  return options;
}

function cleanAscii(value) {
  return value.replace(/[^\x20-\x7e]+/g, " ").trim();
}

function normalizeMac(mac) {
  const compact = mac.replace(/[:-]/g, "").toLowerCase();
  if (!/^[0-9a-f]{12}$/.test(compact)) throw new Error(`Invalid MAC address: ${mac}`);
  return compact.match(/../g).join(":");
}

function reverseMac(buf) {
  return Array.from(buf).reverse().map((b) => b.toString(16).padStart(2, "0")).join(":");
}

function macToReversedBuffer(mac) {
  return Buffer.from(normalizeMac(mac).replace(/:/g, ""), "hex").reverse();
}

function checksum(buffer) {
  let value = 0xbeaf;
  for (const byte of buffer) value = (value + byte) & 0xffff;
  return value;
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

function pad16(payload) {
  const padding = (16 - (payload.length % 16)) % 16;
  return padding === 0 ? payload : Buffer.concat([payload, Buffer.alloc(padding)]);
}

function sendUdp(host, packet, timeoutMs = 3000) {
  return new Promise((resolve, reject) => {
    const socket = dgram.createSocket("udp4");
    const timeout = setTimeout(() => {
      socket.close();
      reject(new Error(`Timeout waiting for ${host}:${DEFAULT_PORT}`));
    }, timeoutMs);

    socket.on("error", (error) => {
      clearTimeout(timeout);
      socket.close();
      reject(error);
    });

    socket.on("message", (message, rinfo) => {
      clearTimeout(timeout);
      socket.close();
      resolve({ message, rinfo });
    });

    socket.bind(0, "0.0.0.0", () => socket.send(packet, DEFAULT_PORT, host));
  });
}

function parseDiscoveryResponse(message, host) {
  if (message.length < 0x40) throw new Error(`Discovery response from ${host} is too short: ${message.length}`);
  const devtype = message.readUInt16LE(0x34);
  const mac = reverseMac(message.subarray(0x3a, 0x40));
  const name = cleanAscii(message.subarray(0x40).toString("latin1").split("\0")[0] || "");
  const isLocked = Boolean(message[0x7f]);
  return { host, devtype, mac, name, isLocked };
}

async function discoverHost(host) {
  const { message } = await sendUdp(host, DISCOVERY_PAYLOAD, 3000);
  return parseDiscoveryResponse(message, host);
}

function buildBroadlinkPacket(device, command, payload, key = INIT_KEY, deviceId = 0) {
  const encrypted = encryptPayload(pad16(payload), key);
  const packet = Buffer.alloc(0x38 + encrypted.length);

  MAGIC.copy(packet, 0);
  packet.writeUInt16LE(device.devtype, 0x24);
  packet.writeUInt16LE(command, 0x26);
  crypto.randomBytes(2).copy(packet, 0x28);
  macToReversedBuffer(device.mac).copy(packet, 0x2a);
  packet.writeUInt32LE(deviceId, 0x30);
  packet.writeUInt16LE(checksum(payload), 0x34);
  encrypted.copy(packet, 0x38);
  packet.writeUInt16LE(checksum(packet), 0x20);

  return packet;
}

async function broadlinkAuth(device) {
  const payload = Buffer.alloc(0x50);
  payload.fill(0x31, 0x04, 0x14);
  payload[0x1e] = 0x01;
  payload[0x2d] = 0x01;
  Buffer.from("Test 1").copy(payload, 0x30);

  const packet = buildBroadlinkPacket(device, 0x0065, payload);
  const { message } = await sendUdp(device.host, packet, 3000);
  const errorCode = message.length >= 0x24 ? message.readUInt16LE(0x22) : -1;
  if (errorCode !== 0) throw new Error(`BroadLink auth error 0x${errorCode.toString(16).padStart(4, "0")}`);

  const plain = decryptPayload(message.subarray(0x38), INIT_KEY);
  return {
    deviceId: plain.readUInt32LE(0),
    key: plain.subarray(0x04, 0x14),
    rawPayloadPrefix: plain.subarray(0, 0x20).toString("hex"),
  };
}

function encodeTclInnerPayload(action, body) {
  const bodyBuffer = Buffer.from(JSON.stringify(body), "utf8");
  const meaningfulLength = 12 + bodyBuffer.length;
  const plainLength = Math.ceil((14 + bodyBuffer.length) / 16) * 16;
  const plain = Buffer.alloc(plainLength);

  plain.writeUInt16LE(meaningfulLength, 0);
  INNER_MAGIC.copy(plain, 2);
  plain[8] = action === "get" ? 1 : 2;
  plain[9] = 0x0b;
  plain.writeUInt32LE(bodyBuffer.length, 10);
  bodyBuffer.copy(plain, 14);
  plain.writeUInt16LE(checksum(Buffer.concat([plain.subarray(2, 6), plain.subarray(8)])), 6);

  return plain;
}

function buildTclPacket(device, auth, deviceId) {
  const plain = encodeTclInnerPayload("get", {});
  const encrypted = encryptPayload(plain, auth.key);
  const packet = Buffer.alloc(0x38 + encrypted.length);

  MAGIC.copy(packet, 0);
  packet.writeUInt16LE(device.devtype, 0x24);
  packet.writeUInt16LE(0x006a, 0x26);
  crypto.randomBytes(2).copy(packet, 0x28);
  macToReversedBuffer(device.mac).copy(packet, 0x2a);
  packet.writeUInt32LE(deviceId, 0x30);
  packet.writeUInt16LE(checksum(plain), 0x34);
  encrypted.copy(packet, 0x38);
  packet.writeUInt16LE(checksum(packet), 0x20);

  return packet;
}

function decodeTclResponse(packet, key) {
  if (packet.length < 72) throw new Error(`TCL response too short: ${packet.length}`);
  const command = packet.readUInt16LE(0x26);
  if (command !== 0x03ee) throw new Error(`Unexpected TCL response command 0x${command.toString(16)}`);

  const errorCode = packet.readUInt16LE(0x22);
  if (errorCode !== 0) throw new Error(`TCL response error 0x${errorCode.toString(16).padStart(4, "0")}`);

  const plain = decryptPayload(packet.subarray(0x38), key);
  const expectedChecksum = packet.readUInt16LE(0x34);
  const actualChecksum = checksum(plain);
  if (expectedChecksum !== actualChecksum) {
    throw new Error(
      `TCL payload checksum mismatch: got 0x${actualChecksum.toString(16)}, expected 0x${expectedChecksum.toString(16)}`,
    );
  }

  const meaningfulLength = plain.readUInt16LE(0);
  const bodyLength = meaningfulLength - 12;
  if (bodyLength < 0 || 14 + bodyLength > plain.length) throw new Error(`Invalid TCL inner length ${meaningfulLength}`);
  return JSON.parse(plain.subarray(14, 14 + bodyLength).toString("utf8"));
}

async function tryTclGet(device, auth, deviceId) {
  const packet = buildTclPacket(device, auth, deviceId);
  const { message } = await sendUdp(device.host, packet, 3000);
  return decodeTclResponse(message, auth.key);
}

function redactKey(key, showKey) {
  const hex = key.toString("hex");
  return showKey ? hex : `${hex.slice(0, 6)}...${hex.slice(-6)}`;
}

const options = parseArgs(process.argv.slice(2));

if (options.help || options.hosts.length === 0) {
  usage();
  process.exit(options.help ? 0 : 2);
}

for (const host of options.hosts) {
  console.log(`\n# ${host}`);
  try {
    const device = await discoverHost(host);
    console.log(
      JSON.stringify(
        {
          discovery: {
            host: device.host,
            devtype: `0x${device.devtype.toString(16).padStart(4, "0")}`,
            name: device.name,
            mac: device.mac,
            is_locked: device.isLocked,
          },
        },
        null,
        2,
      ),
    );

    const auth = await broadlinkAuth(device);
    console.log(
      JSON.stringify(
        {
          auth: {
            ok: true,
            device_id: auth.deviceId,
            key: redactKey(auth.key, options.showKey),
          },
        },
        null,
        2,
      ),
    );

    for (const deviceId of [auth.deviceId, 1]) {
      try {
        const state = await tryTclGet(device, auth, deviceId);
        console.log(
          JSON.stringify(
            {
              tcl_get: {
                ok: true,
                device_id_used: deviceId,
                state_keys: Object.keys(state).sort(),
              },
            },
            null,
            2,
          ),
        );
        break;
      } catch (error) {
        console.log(
          JSON.stringify(
            {
              tcl_get: {
                ok: false,
                device_id_used: deviceId,
                error: error.message,
              },
            },
            null,
            2,
          ),
        );
      }
    }
  } catch (error) {
    console.log(JSON.stringify({ error: error.message }, null, 2));
  }
}
