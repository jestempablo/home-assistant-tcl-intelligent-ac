#!/usr/bin/env node

import crypto from "node:crypto";
import dgram from "node:dgram";
import fs from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { execFile, execFileSync } from "node:child_process";
import { stdin as input, stdout as output } from "node:process";
import readline from "node:readline/promises";
import { fileURLToPath } from "node:url";

const IV = Buffer.from("562e17996d093d28ddb3ba695a2e6f58", "hex");
const INIT_KEY = Buffer.from("097628343fe99e23765c1513accf8b02", "hex");
const MAGIC = Buffer.from("5aa5aa555aa5aa55", "hex");
const INNER_MAGIC = Buffer.from("a5a55a5a", "hex");
const DEFAULT_PORT = 80;
const TCL_DEVICE_TYPE = 0x507c;
const BROADLINK_AUTH_COMMAND = 0x0065;
const TCL_COMMAND = 0x006a;
const RESPONSE_COMMAND = 0x03ee;
const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.dirname(SCRIPT_DIR);
const AC_SOFTAP_PREFIXES = ["Air conditioner_", "Air conditioner-"];
const DEFAULT_SOFTAP_GATEWAY = "192.168.10.1";
const DEFAULT_PROMPT_TIMEOUT_MS = 10000;
const DISCOVERY_PAYLOAD = Buffer.from(
  "5aa5aa555aa5aa5502000000ea07041f0a001805000000001002000a688d0000ffc40000000006000000000000000000",
  "hex",
);
const KNOWN_MAC_PREFIXES = ["24:df:a7", "34:ea:34", "ec:0b:ae", "b4:43:0d", "a0:43:b0"];
const DEFAULT_SOFTAP_HOSTS = ["192.168.4.1", "192.168.10.1", "192.168.1.1", "10.10.100.254"];
const DEFAULT_SOFTAP_WRITE_HOSTS = ["192.168.4.1", "192.168.10.1", "10.10.100.254"];

function usage() {
  console.error(
    [
      "Usage:",
      "  node tools/tcl-ac-provision.mjs probe [--target <host>] [--debug]",
      "  node tools/tcl-ac-provision.mjs softap-scan [--target <host>] [--debug]",
      "  node tools/tcl-ac-provision.mjs ap-list [--target <host>] [--debug]",
      "  node tools/tcl-ac-provision.mjs test-wifi --ssid <ssid> [--password <password>] [--debug]",
      "  node tools/tcl-ac-provision.mjs wizard [--ssid <ssid>] [--password <password>] [--debug]",
      "  node tools/tcl-ac-provision.mjs provision [--ssid <ssid>] [--password <password>] [--dry-run] [--debug]",
      "",
      "Options:",
      "  --target <host>              SoftAP host/gateway to try. Can be repeated.",
      "  --ap-ssid <ssid>             AC hotspot SSID, if Wi-Fi scanning cannot see it.",
      "  --ssid <ssid>                Target 2.4 GHz Wi-Fi SSID for provisioning.",
      "  --password <password>        Target Wi-Fi password. Omit it to use the hidden prompt.",
      "  --timeout-ms <ms>            UDP response wait per stage. Default: 5000.",
      "  --post-wifi-timeout <sec>    LAN discovery time after reconnecting home Wi-Fi. Default: 180.",
      "  --security-mode <0-4>        BroadLink AP setup Wi-Fi security. Default: 3 (WPA2).",
      "  --ports <csv>                Ports for softap-scan. Default: 80,12414,16680,16384,8080,8000,5000,443.",
      "  --diagnostics                In wizard, run extra SoftAP probes before sending Wi-Fi config.",
      "  --no-auto-wifi               Do not call macOS networksetup to switch Wi-Fi networks.",
      "  --debug                      Write redacted packet logs to logs/tcl-ac-provision-*.jsonl.",
      "  --dry-run                    For wizard/provision: stop before sending AP config write attempts.",
      "  --yes                        Do not pause for interactive confirmations.",
      "  -h, --help                   Show this help.",
      "",
      "Notes:",
      "  This is experimental reverse-engineering tooling. It does not add devices to the Intelligent AC cloud.",
      "  Put only one AC in pairing mode while testing.",
      "  Wizard mode is manual: connect this computer to the AC hotspot first, then run the guided send.",
    ].join("\n"),
  );
}

function parseArgs(argv) {
  const options = {
    command: null,
    targets: [],
    timeoutMs: 5000,
    postWifiTimeoutSec: 180,
    ports: [80, 12414, 16680, 16384, 8080, 8000, 5000, 443],
    securityMode: 3,
    diagnostics: false,
    debug: false,
    dryRun: false,
    yes: false,
    autoWifi: true,
    explicitHelp: false,
    usageError: false,
  };

  let startIndex = 0;
  if (argv[0] && !argv[0].startsWith("-")) {
    options.command = argv[0];
    startIndex = 1;
  }

  for (let i = startIndex; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--target") {
      options.targets.push(readOptionValue(argv, i, arg));
      i += 1;
    } else if (arg === "--ap-ssid") {
      options.apSsid = readOptionValue(argv, i, arg);
      i += 1;
    } else if (arg === "--ssid") {
      options.ssid = readOptionValue(argv, i, arg);
      i += 1;
    } else if (arg === "--password") {
      options.password = readOptionValue(argv, i, arg);
      i += 1;
    } else if (arg === "--timeout-ms") {
      options.timeoutMs = Number(readOptionValue(argv, i, arg));
      i += 1;
    } else if (arg === "--post-wifi-timeout") {
      options.postWifiTimeoutSec = Number(readOptionValue(argv, i, arg));
      i += 1;
    } else if (arg === "--security-mode") {
      options.securityMode = Number(readOptionValue(argv, i, arg));
      i += 1;
    } else if (arg === "--ports") {
      options.ports = readOptionValue(argv, i, arg)
        .split(",")
        .map((value) => Number(value.trim()))
        .filter((value) => Number.isInteger(value) && value > 0 && value <= 65535);
      i += 1;
    } else if (arg === "--diagnostics") {
      options.diagnostics = true;
    } else if (arg === "--debug") {
      options.debug = true;
    } else if (arg === "--dry-run") {
      options.dryRun = true;
    } else if (arg === "--yes") {
      options.yes = true;
    } else if (arg === "--no-auto-wifi") {
      options.autoWifi = false;
    } else if (arg === "-h" || arg === "--help") {
      options.explicitHelp = true;
    } else {
      throw new Error(`Unknown argument: ${arg}`);
    }
  }

  if (options.explicitHelp) {
    options.help = true;
    return options;
  }

  if (!["probe", "softap-scan", "ap-list", "test-wifi", "wizard", "provision"].includes(options.command)) {
    options.help = true;
    options.usageError = true;
  }
  if (!Number.isFinite(options.timeoutMs) || options.timeoutMs <= 0) {
    throw new Error("--timeout-ms must be a positive number.");
  }
  if (!Number.isFinite(options.postWifiTimeoutSec) || options.postWifiTimeoutSec <= 0) {
    throw new Error("--post-wifi-timeout must be a positive number.");
  }
  if (!Number.isInteger(options.securityMode) || options.securityMode < 0 || options.securityMode > 4) {
    throw new Error("--security-mode must be 0, 1, 2, 3, or 4.");
  }
  if (options.ports.length === 0) {
    throw new Error("--ports must contain at least one valid TCP/UDP port.");
  }
  return options;
}

function readOptionValue(argv, index, flag) {
  if (index + 1 >= argv.length) throw new Error(`${flag} requires a value.`);
  return argv[index + 1];
}

function cleanAscii(value) {
  return value.replace(/[^\x20-\x7e]+/g, " ").trim();
}

function checksum(buffer) {
  let value = 0xbeaf;
  for (const byte of buffer) value = (value + byte) & 0xffff;
  return value;
}

function pad16(payload) {
  const padding = (16 - (payload.length % 16)) % 16;
  return padding === 0 ? payload : Buffer.concat([payload, Buffer.alloc(padding)]);
}

function normalizeMac(mac) {
  const compact = mac.replace(/[:-]/g, "").toLowerCase();
  if (!/^[0-9a-f]{12}$/.test(compact)) throw new Error(`Invalid MAC address: ${mac}`);
  return compact.match(/../g).join(":");
}

function reverseMac(buf) {
  return Array.from(buf)
    .reverse()
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join(":");
}

function macToReversedBuffer(mac) {
  return Buffer.from(normalizeMac(mac).replace(/:/g, ""), "hex").reverse();
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

function redact(value, secrets = []) {
  let text = typeof value === "string" ? value : JSON.stringify(value ?? {});
  for (const secret of secrets) {
    for (const variant of secretVariants(secret)) {
      text = text.split(variant).join("<redacted>");
      text = text.split(variant.toUpperCase()).join("<redacted>");
    }
  }
  text = text.replace(/"password"\s*:\s*"([^"\\]|\\.)*"/gi, '"password":"<redacted>"');
  text = text.replace(/"key"\s*:\s*"[0-9a-f]{32}"/gi, '"key":"<redacted>"');
  text = text.replace(/"devkey"\s*:\s*"[0-9a-f]{32}"/gi, '"devkey":"<redacted>"');
  text = text.replace(/"token"\s*:\s*"[0-9a-f]{32}"/gi, '"token":"<redacted>"');
  return text;
}

function secretVariants(secret) {
  if (!secret) return [];
  const raw = String(secret);
  const jsonEscaped = JSON.stringify(raw).slice(1, -1);
  return unique([raw, jsonEscaped, Buffer.from(raw, "utf8").toString("hex"), Buffer.from(jsonEscaped, "utf8").toString("hex")]);
}

function createLogger(options) {
  if (!options.debug) {
    return { path: null, write() {} };
  }

  const dir = path.join(REPO_ROOT, "logs");
  fs.mkdirSync(dir, { recursive: true });
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  const logPath = path.join(dir, `tcl-ac-provision-${stamp}.jsonl`);
  return {
    path: logPath,
    write(event, data = {}, secrets = []) {
      const record = {
        ts: new Date().toISOString(),
        event,
        ...JSON.parse(redact(data, secrets)),
      };
      fs.appendFileSync(logPath, `${JSON.stringify(record)}\n`);
    },
  };
}

function runText(command, args) {
  try {
    return execFileSync(command, args, { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }).trim();
  } catch {
    return "";
  }
}

function runTextAsync(command, args, timeoutMs = 15000) {
  return new Promise((resolve) => {
    execFile(command, args, { encoding: "utf8", timeout: timeoutMs }, (error, stdout) => {
      resolve(error ? "" : stdout.trim());
    });
  });
}

function runCommand(command, args) {
  try {
    const stdout = execFileSync(command, args, { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] }).trim();
    return { ok: true, stdout };
  } catch (error) {
    const stderr = error.stderr ? error.stderr.toString("utf8").trim() : "";
    return { ok: false, error: cleanAscii(stderr || error.message || String(error)) };
  }
}

function wifiInterface() {
  if (process.platform !== "darwin") return null;
  const text = runText("networksetup", ["-listallhardwareports"]);
  for (const block of text.split(/\n\s*\n/)) {
    if (!/Hardware Port:\s*(Wi-Fi|AirPort)/i.test(block)) continue;
    const match = block.match(/Device:\s*(\S+)/);
    if (match) return match[1];
  }
  return null;
}

function currentWifiSsid() {
  if (process.platform === "darwin") {
    for (const iface of unique([wifiInterface(), "en0", "en1"])) {
      const text = runText("networksetup", ["-getairportnetwork", iface]);
      const match = text.match(/Current Wi-Fi Network:\s*(.+)$/);
      if (match) return match[1].trim();

      const ipconfig = runText("ipconfig", ["getsummary", iface]);
      const ipconfigMatch = ipconfig.match(/^\s*SSID\s*:\s*(.+)$/m);
      if (ipconfigMatch) return ipconfigMatch[1].trim();
    }

    const airport = airportCommandPath();
    if (airport) {
      const text = runText(airport, ["-I"]);
      const match = text.match(/^\s*SSID:\s*(.+)$/m);
      if (match) return match[1].trim();
    }
  }

  const linux = runText("iwgetid", ["-r"]);
  return linux || null;
}

function addAirportSsids(text, ssids) {
  for (const line of text.split("\n").slice(1)) {
    const match = line.match(/^\s*(.*?)\s+([0-9a-f]{2}:){5}[0-9a-f]{2}\s+/i);
    if (match?.[1]?.trim()) ssids.add(match[1].trim());
  }
}

function addSystemProfilerSsids(text, ssids) {
  for (const line of text.split("\n")) {
    const match = line.match(/^\s{8,}([^:][^:]+):\s*$/);
    const ssid = match?.[1]?.trim();
    if (!ssid) continue;
    if (["Current Network Information", "Other Local Wi-Fi Networks", "Software Versions", "Interfaces"].includes(ssid)) continue;
    ssids.add(ssid);
  }
}

function airportCommandPath() {
  const airport = "/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport";
  return fs.existsSync(airport) ? airport : null;
}

async function scanWifiSsidsAsync() {
  if (process.platform !== "darwin") return [];
  const ssids = new Set();
  const airport = airportCommandPath();
  if (airport) {
    addAirportSsids(await runTextAsync(airport, ["-s"], 10000), ssids);
  }

  addSystemProfilerSsids(await runTextAsync("system_profiler", ["SPAirPortDataType"], 12000), ssids);

  return [...ssids];
}

function looksLikeAcSoftAp(ssid) {
  return Boolean(ssid && AC_SOFTAP_PREFIXES.some((prefix) => ssid.startsWith(prefix)));
}

function findAcSoftApSsids(ssids) {
  return unique(ssids.filter(looksLikeAcSoftAp));
}

function connectWifiNetwork(ssid, password = "") {
  if (process.platform !== "darwin") {
    return { ok: false, error: "Automatic Wi-Fi switching is implemented only for macOS in this tool." };
  }
  const iface = wifiInterface();
  if (!iface) return { ok: false, error: "Could not detect the macOS Wi-Fi interface." };
  const args = ["-setairportnetwork", iface, ssid];
  if (password) args.push(password);
  return { iface, ...runCommand("networksetup", args) };
}

async function sleep(ms) {
  await new Promise((resolve) => setTimeout(resolve, ms));
}

function createSpinner(message) {
  const frames = ["|", "/", "-", "\\"];
  const enabled = Boolean(process.stderr.isTTY);
  let timer = null;
  let index = 0;
  let text = message;
  let width = 0;

  function render() {
    const line = `${frames[index % frames.length]} ${text}`;
    index += 1;
    width = Math.max(width, line.length);
    process.stderr.write(`\r${line}${" ".repeat(Math.max(0, width - line.length))}`);
  }

  return {
    start() {
      if (!enabled) {
        console.error(text);
        return;
      }
      render();
      timer = setInterval(render, 120);
    },
    update(nextText) {
      text = nextText;
      if (!enabled) console.error(text);
    },
    stop(finalText = "") {
      if (timer) clearInterval(timer);
      timer = null;
      if (enabled) {
        const line = finalText || text;
        process.stderr.write(`\r${line}${" ".repeat(Math.max(0, width - line.length))}\n`);
      } else if (finalText) {
        console.error(finalText);
      }
    },
  };
}

async function waitForLocalAddress(prefix, timeoutMs) {
  return waitForLocalAddressPrefixes([prefix], timeoutMs);
}

async function waitForLocalAddressPrefixes(prefixes, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  const label = prefixes.length === 1 ? `${prefixes[0]}x` : prefixes.map((prefix) => `${prefix}x`).join(" or ");
  const spinner = createSpinner(`Waiting for local address ${label}`);
  spinner.start();
  while (Date.now() < deadline) {
    const address = localIPv4Addresses().find((value) => prefixes.some((prefix) => value.startsWith(prefix)));
    if (address) {
      spinner.stop(`Local address detected: ${address}`);
      return address;
    }
    await sleep(1000);
  }
  spinner.stop(`Local address ${label} was not detected.`);
  return null;
}

function localAddressForPrefixes(prefixes) {
  return localIPv4Addresses().find((value) => prefixes.some((prefix) => value.startsWith(prefix))) || null;
}

async function waitForWifiNetworkReady(ssid, timeoutMs, logger) {
  const deadline = Date.now() + timeoutMs;
  const spinner = createSpinner(`Waiting for Wi-Fi connection to "${ssid}"`);
  spinner.start();

  while (Date.now() < deadline) {
    const current = currentWifiSsid();
    const addresses = localIPv4Addresses().filter((address) => !address.startsWith("192.168.10."));
    const gateway = defaultGateway();
    const connectedByName = current === ssid;
    const usableLan = addresses.length > 0 && Boolean(gateway);

    logger.write(
      "target_wifi_status",
      {
        target: ssid,
        current,
        addresses,
        gateway,
        connectedByName,
        usableLan,
      },
      [ssid],
    );

    if (connectedByName || usableLan) {
      const status = { ok: true, current, addresses, gateway, connectedByName, usableLan };
      spinner.stop(
        connectedByName
          ? `Connected to "${ssid}" (${addresses.join(", ") || "no IP reported yet"})`
          : `Wi-Fi has LAN connectivity (${current || "SSID unknown"}, ${addresses.join(", ") || "no IP reported"})`,
      );
      return status;
    }

    await sleep(1000);
  }

  const status = {
    ok: false,
    current: currentWifiSsid(),
    addresses: localIPv4Addresses(),
    gateway: defaultGateway(),
    connectedByName: false,
    usableLan: false,
  };
  spinner.stop(`Timed out waiting for Wi-Fi connection to "${ssid}".`);
  return status;
}

async function waitForAcSoftApSsid(options, logger, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  const spinner = createSpinner(`Scanning for AC hotspot (${AC_SOFTAP_PREFIXES.join(" or ")}******)`);
  spinner.start();

  while (Date.now() < deadline) {
    const current = currentWifiSsid();
    if (looksLikeAcSoftAp(current)) {
      spinner.stop(`Already connected to AC hotspot "${current}".`);
      logger.write("ac_softap_scan", { current, matches: [current], selected: current });
      return current;
    }

    const visibleSsids = await scanWifiSsidsAsync();
    const matches = findAcSoftApSsids(visibleSsids);
    logger.write(
      "ac_softap_scan",
      {
        current: current ? "<non-ac-wifi>" : null,
        visibleCount: visibleSsids.length,
        matches,
        selected: matches[0] || null,
      },
    );

    if (matches.length > 0) {
      spinner.stop(`AC hotspot found: "${matches[0]}".`);
      return matches[0];
    }

    const remainingSec = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
    spinner.update(`Scanning for AC hotspot (${remainingSec}s left)`);
    await sleep(3000);
  }

  spinner.stop("AC hotspot was not found in Wi-Fi scans.");
  return null;
}

async function waitForSoftApConnection(options, logger, timeoutMs) {
  const prefixes = softApLocalPrefixes(options);
  const deadline = Date.now() + timeoutMs;
  const spinner = createSpinner("Waiting for AC hotspot connection");
  spinner.start();

  while (Date.now() < deadline) {
    const current = currentWifiSsid();
    const localAddress = localAddressForPrefixes(prefixes);
    const connectedByName = looksLikeAcSoftAp(current);
    const connectedByAddress = Boolean(localAddress);

    logger.write("softap_connection_status", {
      current: connectedByName ? current : current ? "<non-ac-wifi>" : null,
      localAddress,
      connectedByName,
      connectedByAddress,
    });

    if (connectedByName || connectedByAddress) {
      spinner.stop(
        connectedByAddress
          ? `AC hotspot connection detected by local address: ${localAddress}`
          : `AC hotspot connection detected by SSID: "${current}"`,
      );
      return {
        ok: true,
        ssid: connectedByName ? current : null,
        localAddress,
        connectedByName,
        connectedByAddress,
      };
    }

    const remainingSec = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
    spinner.update(`Waiting for AC hotspot connection (${remainingSec}s left)`);
    await sleep(1000);
  }

  spinner.stop("AC hotspot connection was not detected.");
  return { ok: false, ssid: null, localAddress: null, connectedByName: false, connectedByAddress: false };
}

function defaultGateway() {
  if (process.platform === "darwin") {
    const text = runText("route", ["-n", "get", "default"]);
    const match = text.match(/gateway:\s*([0-9.]+)/);
    if (match) return match[1];
  }

  const linux = runText("ip", ["route", "show", "default"]);
  const match = linux.match(/default via\s+([0-9.]+)/);
  return match ? match[1] : null;
}

function localIPv4Addresses() {
  return Object.values(os.networkInterfaces())
    .flat()
    .filter((info) => info && info.family === "IPv4" && !info.internal)
    .map((info) => info.address);
}

function subnetPrefixFromIpv4(address) {
  const parts = String(address).split(".");
  if (parts.length !== 4 || parts.some((part) => !/^\d+$/.test(part))) return null;
  return `${parts[0]}.${parts[1]}.${parts[2]}.`;
}

function unique(values) {
  return [...new Set(values.filter(Boolean))];
}

function softApTargets(options) {
  const hosts = options.targets.length > 0 ? options.targets : [defaultGateway(), ...subnetGateways(), ...DEFAULT_SOFTAP_HOSTS];
  return unique(hosts).map((host) => ({
    host,
    port: DEFAULT_PORT,
  }));
}

function softApWriteTargets(options) {
  const hosts = options.targets.length > 0 ? options.targets : DEFAULT_SOFTAP_WRITE_HOSTS;
  return unique(hosts).map((host) => ({
    host,
    port: DEFAULT_PORT,
  }));
}

function softApLocalPrefixes(options) {
  return unique([...softApWriteTargets(options).map(({ host }) => subnetPrefixFromIpv4(host)), "192.168.10."]);
}

function subnetGateways() {
  return localIPv4Addresses().flatMap((address) => {
    const parts = address.split(".").map(Number);
    if (parts.length !== 4 || parts.some((part) => !Number.isInteger(part))) return [];
    return [`${parts[0]}.${parts[1]}.${parts[2]}.1`, `${parts[0]}.${parts[1]}.${parts[2]}.254`];
  });
}

function localDiscoveryTargets(seedHosts = []) {
  return unique([...seedHosts, "255.255.255.255", "224.0.0.251"]).flatMap((host) => {
    if (host === "224.0.0.251") return [{ host, port: DEFAULT_PORT }, { host, port: 16680 }];
    return [{ host, port: DEFAULT_PORT }];
  });
}

function parsePossibleJson(buffer) {
  const text = buffer.toString("utf8").trim().replace(/\0+$/g, "");
  if (!text.startsWith("{") && !text.startsWith("[")) return null;
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

function parseJsonWithPadding(buffer) {
  const text = buffer.toString("utf8");
  const start = text.indexOf("{");
  const end = text.lastIndexOf("}");
  if (start < 0 || end < start) return null;
  try {
    return JSON.parse(text.slice(start, end + 1));
  } catch {
    return null;
  }
}

function macFromDid(did) {
  if (!did) return null;
  const compact = String(did).replace(/[^0-9a-f]/gi, "").toLowerCase();
  if (compact.length < 12) return null;
  return compact
    .slice(-12)
    .match(/../g)
    .join(":");
}

function parseStandardSetupResponse(message) {
  if (message.length < 0x40 || message.readUInt16LE(0x26) !== 0x0015) return null;
  const encrypted = message.subarray(0x30);
  if (encrypted.length % 16 !== 0) return null;

  try {
    const plain = decryptPayload(encrypted, INIT_KEY);
    const json = parseJsonWithPadding(plain);
    if (!json || typeof json !== "object" || !json.devkey) return null;
    return {
      ...json,
      mac: macFromDid(json.did),
    };
  } catch {
    return null;
  }
}

function redactedSetupInfo(info) {
  if (!info) return null;
  return {
    hw: info.hw,
    svn: info.svn,
    buildtime: info.buildtime,
    ver: info.ver,
    pid: info.pid,
    did: info.did,
    mac: info.mac,
    uptime: info.uptime,
    ssid: info.ssid,
    bssid: info.bssid,
    rssi: info.rssi,
    devkey: info.devkey ? "<redacted>" : undefined,
  };
}

function parseDiscoveryResponse(message, host) {
  if (message.length < 0x40) return null;
  const devtype = message.readUInt16LE(0x34);
  const mac = reverseMac(message.subarray(0x3a, 0x40));
  const name = cleanAscii(message.subarray(0x40).toString("latin1").split("\0")[0] || "");
  const isKnownMac = KNOWN_MAC_PREFIXES.some((prefix) => mac.toLowerCase().startsWith(prefix));
  if (!isKnownMac && devtype !== TCL_DEVICE_TYPE) return null;
  return {
    host,
    devtype,
    mac,
    name: name || `TCL AC ${mac.replace(/:/g, "").slice(-6)}`,
    isLocked: Boolean(message[0x7f]),
  };
}

function summarizeResponse(message, rinfo) {
  const json = parsePossibleJson(message);
  const discovery = parseDiscoveryResponse(message, rinfo.address);
  const setup = parseStandardSetupResponse(message);
  return {
    from: `${rinfo.address}:${rinfo.port}`,
    length: message.length,
    json,
    discovery: discovery
      ? {
          ...discovery,
          devtype: `0x${discovery.devtype.toString(16).padStart(4, "0")}`,
        }
      : null,
    setup: redactedSetupInfo(setup),
    ascii: cleanAscii(message.toString("latin1")).slice(0, 300),
    hex: message.toString("hex"),
  };
}

function buildExperimentalFrame(command, payload) {
  const packet = Buffer.alloc(0x38 + payload.length);
  MAGIC.copy(packet, 0);
  packet.writeUInt16LE(command, 0x26);
  crypto.randomBytes(2).copy(packet, 0x28);
  packet.writeUInt16LE(checksum(payload), 0x34);
  payload.copy(packet, 0x38);
  packet.writeUInt16LE(checksum(packet), 0x20);
  return packet;
}

function appApConfigJson(ssid, password, timeoutMs = 4000) {
  return {
    ssid,
    password,
    type: 3,
    timeout: timeoutMs,
    protocol: 0,
    pubkey: null,
    sendcount: 1,
  };
}

function apListJson(timeoutMs = 7000) {
  return {
    timeout: Math.max(timeoutMs, 7000),
    sendcount: 1,
  };
}

function pubKeyJson(timeoutMs = 2000) {
  return {
    timeout: timeoutMs,
  };
}

function buildProbeAttempts(targets) {
  return targets.flatMap(({ host, port }) => [
    { name: "broadlink-discovery", host, port, payload: DISCOVERY_PAYLOAD },
    {
      name: "raw-json-ping",
      host,
      port,
      payload: Buffer.from(JSON.stringify({ timeout: 2000, sendcount: 1 }), "utf8"),
    },
  ]);
}

function buildUdpScanAttempts(hosts, ports) {
  const probes = [
    { name: "broadlink-discovery", payload: DISCOVERY_PAYLOAD },
    { name: "raw-json-ping", payload: Buffer.from(JSON.stringify({ timeout: 1000, sendcount: 1 }), "utf8") },
    { name: "empty", payload: Buffer.alloc(0) },
  ];

  return hosts.flatMap((host) =>
    ports.flatMap((port) =>
      probes.map((probe) => ({
        name: `udp-scan-${probe.name}`,
        host,
        port,
        payload: probe.payload,
      })),
    ),
  );
}

function buildApListAttempts(targets) {
  const listPayload = Buffer.from(JSON.stringify(apListJson(7000)), "utf8");
  const pubKeyPayload = Buffer.from(JSON.stringify(pubKeyJson(2000)), "utf8");
  return targets.flatMap(({ host, port }) => [
    { name: "ap-list-json-udp", host, port, payload: listPayload },
    { name: "ap-list-frame-0x001a", host, port, payload: buildExperimentalFrame(0x001a, listPayload) },
    { name: "pubkey-json-udp", host, port, payload: pubKeyPayload },
    { name: "pubkey-frame-0x003a", host, port, payload: buildExperimentalFrame(0x003a, pubKeyPayload) },
    { name: "discovery-after-ap-list", host, port, payload: DISCOVERY_PAYLOAD },
  ]);
}

function buildProvisionAttempts(targets, provisionJson) {
  const payload = Buffer.from(JSON.stringify(provisionJson), "utf8");
  return targets.flatMap(({ host, port }) => [
    { name: "ap-config-json-udp", host, port, payload },
    { name: "ap-config-frame-0x0014", host, port, payload: buildExperimentalFrame(0x0014, payload) },
    { name: "ap-config-frame-0x003a", host, port, payload: buildExperimentalFrame(0x003a, payload) },
  ]);
}

function buildStandardBroadlinkSetupPayload(ssid, password, securityMode) {
  const ssidBuffer = Buffer.from(ssid, "utf8");
  const passwordBuffer = Buffer.from(password, "utf8");
  const payload = Buffer.alloc(0x88);

  payload[0x26] = 0x14;
  ssidBuffer.copy(payload, 0x44);
  passwordBuffer.copy(payload, 0x64);
  payload[0x84] = ssidBuffer.length;
  payload[0x85] = passwordBuffer.length;
  payload[0x86] = securityMode;

  const payloadChecksum = checksum(payload);
  payload.writeUInt16LE(payloadChecksum, 0x20);
  return payload;
}

function softApBroadcastTargets(options) {
  const targetPrefixes = softApLocalPrefixes(options);
  const softApAddresses = localIPv4Addresses().filter((address) => targetPrefixes.some((prefix) => address.startsWith(prefix)));
  const localBroadcasts = softApAddresses.flatMap((address) => {
    const parts = address.split(".").map(Number);
    if (parts.length !== 4 || parts.some((part) => !Number.isInteger(part))) return [];
    return `${parts[0]}.${parts[1]}.${parts[2]}.255`;
  });

  return unique([
    ...softApWriteTargets(options).map(({ host }) => host),
    ...localBroadcasts,
    ...(softApAddresses.length > 0 ? ["255.255.255.255"] : []),
  ]).map((host) => ({ host, port: DEFAULT_PORT }));
}

async function sendUdpAttempts(attempts, waitMs, logger, secrets = []) {
  const responses = [];
  const sent = [];

  await new Promise((resolve) => {
    const socket = dgram.createSocket({ type: "udp4", reuseAddr: true });
    const seen = new Set();
    let resolved = false;

    function finish() {
      if (resolved) return;
      resolved = true;
      socket.close();
      resolve();
    }

    socket.on("error", (error) => {
      logger.write("udp_socket_error", { error: error.message }, secrets);
    });

    socket.on("message", (message, rinfo) => {
      const key = `${rinfo.address}:${rinfo.port}:${message.toString("hex")}`;
      if (seen.has(key)) return;
      seen.add(key);
      const summary = summarizeResponse(message, rinfo);
      responses.push(summary);
      logger.write("udp_response", summary, secrets);
      console.log(JSON.stringify(summary, null, 2));
    });

    socket.bind(0, "0.0.0.0", () => {
      socket.setBroadcast(true);
      for (const attempt of attempts) {
        sent.push({ name: attempt.name, target: `${attempt.host}:${attempt.port}`, bytes: attempt.payload.length });
        logger.write(
          "udp_send",
          {
            name: attempt.name,
            target: `${attempt.host}:${attempt.port}`,
            bytes: attempt.payload.length,
            hex: secrets.length > 0 ? "<redacted>" : attempt.payload.toString("hex"),
            ascii: secrets.length > 0 ? "<redacted>" : cleanAscii(attempt.payload.toString("latin1")),
          },
          secrets,
        );
        socket.send(attempt.payload, attempt.port, attempt.host, (error) => {
          if (error) {
            logger.write("udp_send_error", { name: attempt.name, target: `${attempt.host}:${attempt.port}`, error: error.message }, secrets);
          }
        });
      }
    });

    setTimeout(finish, waitMs);
  });

  return { sent, responses };
}

async function sendTcpJson(targets, name, body, waitMs, logger, secrets = []) {
  const payload = Buffer.from(`${JSON.stringify(body)}\n`, "utf8");

  const results = await Promise.all(
    targets.map(({ host, port }) =>
      new Promise((resolve) => {
        const responses = [];
        const socket = net.createConnection({ host, port, timeout: waitMs });
        const chunks = [];

        socket.on("connect", () => {
          logger.write("tcp_send", { name, target: `${host}:${port}`, ascii: secrets.length > 0 ? "<redacted>" : payload.toString("utf8") }, secrets);
          socket.write(payload);
        });
        socket.on("data", (chunk) => chunks.push(chunk));
        socket.on("timeout", () => socket.destroy());
        socket.on("error", (error) => {
          logger.write("tcp_error", { name, target: `${host}:${port}`, error: error.message }, secrets);
        });
        socket.on("close", () => {
          if (chunks.length > 0) {
            const message = Buffer.concat(chunks);
            const summary = summarizeResponse(message, { address: host, port });
            responses.push(summary);
            logger.write("tcp_response", summary, secrets);
            console.log(JSON.stringify(summary, null, 2));
          }
          resolve(responses);
        });
      }),
    ),
  );

  return results.flat();
}

async function scanTcpPorts(hosts, ports, timeoutMs) {
  const checks = await Promise.all(
    hosts.flatMap((host) =>
      ports.map(
        (port) =>
          new Promise((resolve) => {
            const socket = net.createConnection({ host, port, timeout: timeoutMs });
            let done = false;

            function finish(status, detail = null) {
              if (done) return;
              done = true;
              socket.destroy();
              resolve({ host, port, status, detail });
            }

            socket.on("connect", () => finish("open"));
            socket.on("timeout", () => finish("timeout"));
            socket.on("error", (error) => finish("error", error.code || error.message));
          }),
      ),
    ),
  );

  return checks;
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
      if (rinfo.address !== host) return;
      clearTimeout(timeout);
      socket.close();
      resolve({ message, rinfo });
    });

    socket.bind(0, "0.0.0.0", () => socket.send(packet, DEFAULT_PORT, host));
  });
}

async function broadlinkAuth(device, timeoutMs = 3000) {
  if (device.isLocked) {
    throw new Error("Device reports LOCKED=True and blocks BroadLink/DNA local auth");
  }

  const payload = Buffer.alloc(0x50);
  payload.fill(0x31, 0x04, 0x14);
  payload[0x1e] = 0x01;
  payload[0x2d] = 0x01;
  Buffer.from("Test 1").copy(payload, 0x30);

  const packet = buildBroadlinkPacket(device, BROADLINK_AUTH_COMMAND, payload);
  const { message } = await sendUdp(device.host, packet, timeoutMs);
  const errorCode = message.length >= 0x24 ? message.readUInt16LE(0x22) : -1;
  if (errorCode !== 0) throw new Error(`BroadLink auth error 0x${errorCode.toString(16).padStart(4, "0")}`);

  const plain = decryptPayload(message.subarray(0x38), INIT_KEY);
  return {
    deviceId: plain.readUInt32LE(0),
    key: plain.subarray(0x04, 0x14),
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
  return buildBroadlinkPacket(device, TCL_COMMAND, plain, auth.key, deviceId);
}

function decodeTclResponse(packet, key) {
  if (packet.length < 72) throw new Error(`TCL response too short: ${packet.length}`);
  const command = packet.readUInt16LE(0x26);
  if (command !== RESPONSE_COMMAND) throw new Error(`Unexpected TCL response command 0x${command.toString(16)}`);

  const errorCode = packet.readUInt16LE(0x22);
  if (errorCode !== 0) throw new Error(`TCL response error 0x${errorCode.toString(16).padStart(4, "0")}`);

  const plain = decryptPayload(packet.subarray(0x38), key);
  const expectedChecksum = packet.readUInt16LE(0x34);
  const actualChecksum = checksum(plain);
  if (expectedChecksum !== actualChecksum) {
    throw new Error(`TCL payload checksum mismatch: got 0x${actualChecksum.toString(16)}, expected 0x${expectedChecksum.toString(16)}`);
  }

  const meaningfulLength = plain.readUInt16LE(0);
  const bodyLength = meaningfulLength - 12;
  if (bodyLength < 0 || 14 + bodyLength > plain.length) throw new Error(`Invalid TCL inner length ${meaningfulLength}`);
  return JSON.parse(plain.subarray(14, 14 + bodyLength).toString("utf8"));
}

async function tryTclGet(device, auth, deviceId, timeoutMs = 3000) {
  const packet = buildTclPacket(device, auth, deviceId);
  const { message } = await sendUdp(device.host, packet, timeoutMs);
  return decodeTclResponse(message, auth.key);
}

async function discoverLanDevices(timeoutMs = 5000, seedHosts = []) {
  const targets = localDiscoveryTargets(seedHosts);
  const attempts = targets.map(({ host, port }) => ({
    name: "lan-discovery",
    host,
    port,
    payload: DISCOVERY_PAYLOAD,
  }));
  const logger = { write() {} };
  const { responses } = await sendUdpAttempts(attempts, timeoutMs, logger);
  const devicesByMac = new Map();

  for (const response of responses) {
    if (!response.discovery) continue;
    const devtype = Number.parseInt(response.discovery.devtype, 16);
    if (devtype !== TCL_DEVICE_TYPE) continue;
    devicesByMac.set(response.discovery.mac, {
      host: response.from.split(":")[0],
      devtype,
      mac: response.discovery.mac,
      name: response.discovery.name,
      isLocked: response.discovery.isLocked,
    });
  }

  return [...devicesByMac.values()];
}

async function discoverAuthenticatedLanDevices(timeoutMs = 120000) {
  const deadline = Date.now() + timeoutMs;
  const authenticated = [];
  const authenticatedMacs = new Set();
  const lastFailureLogAt = new Map();
  const spinner = createSpinner(`Polling LAN discovery for TCL AC devices for up to ${Math.round(timeoutMs / 1000)}s`);
  spinner.start();

  while (Date.now() < deadline && authenticated.length === 0) {
    const remainingSec = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
    spinner.update(`Polling LAN discovery for TCL AC devices (${remainingSec}s left)`);
    const devices = await discoverLanDevices(3000);
    for (const device of devices) {
      if (authenticatedMacs.has(device.mac)) continue;
      if (device.isLocked) {
        const lastLogAt = lastFailureLogAt.get(device.mac) || 0;
        if (Date.now() - lastLogAt > 15000) {
          lastFailureLogAt.set(device.mac, Date.now());
          console.error(
            `LAN auth blocked for ${device.host} (${device.mac}): device reports LOCKED=True; clear the lock/reset the Wi-Fi module and retry.`,
          );
        }
        continue;
      }
      try {
        const auth = await broadlinkAuth(device, 3000);
        let state = null;
        let deviceIdUsed = auth.deviceId;
        for (const candidateId of [auth.deviceId, 1]) {
          try {
            state = await tryTclGet(device, auth, candidateId, 3000);
            deviceIdUsed = candidateId;
            break;
          } catch {
            // Try the next known-good ID candidate.
          }
        }
        if (state) {
          authenticatedMacs.add(device.mac);
          authenticated.push({ device, auth, deviceIdUsed, state });
        }
      } catch (error) {
        const lastLogAt = lastFailureLogAt.get(device.mac) || 0;
        if (Date.now() - lastLogAt > 15000) {
          lastFailureLogAt.set(device.mac, Date.now());
          console.error(`LAN auth failed for ${device.host} (${device.mac}): ${error.message}`);
        }
      }
    }

    if (authenticated.length === 0) {
      await new Promise((resolve) => setTimeout(resolve, 3000));
    }
  }
  spinner.stop(authenticated.length > 0 ? `LAN discovery authenticated ${authenticated.length} TCL AC device(s).` : "LAN discovery did not find an authenticated TCL AC device.");
  return authenticated;
}

async function ask(question) {
  const rl = readline.createInterface({ input, output });
  try {
    return (await rl.question(question)).trim();
  } finally {
    rl.close();
  }
}

async function askWithTimeout(question, timeoutMs, defaultValue = "") {
  if (!input.isTTY || timeoutMs <= 0) {
    return { answer: await ask(question), timedOut: false };
  }

  const rl = readline.createInterface({ input, output });
  let timer = null;
  try {
    const questionPromise = rl.question(question).then((answer) => ({
      answer,
      timedOut: false,
    }));
    const timeoutPromise = new Promise((resolve) => {
      timer = setTimeout(() => resolve({ answer: defaultValue, timedOut: true }), timeoutMs);
    });
    const result = await Promise.race([questionPromise, timeoutPromise]);
    if (result.timedOut) output.write("\n");
    return {
      answer: String(result.answer ?? "").trim(),
      timedOut: result.timedOut,
    };
  } finally {
    if (timer) clearTimeout(timer);
    rl.close();
  }
}

async function askHidden(question) {
  if (!input.isTTY) return ask(question);
  output.write(question);
  input.setRawMode(true);
  input.resume();
  return new Promise((resolve) => {
    let value = "";
    const onData = (chunk) => {
      const char = chunk.toString("utf8");
      if (char === "\r" || char === "\n") {
        input.setRawMode(false);
        input.off("data", onData);
        output.write("\n");
        resolve(value);
      } else if (char === "\u0003") {
        input.setRawMode(false);
        input.off("data", onData);
        output.write("\n");
        process.exit(130);
      } else if (char === "\u007f") {
        value = value.slice(0, -1);
      } else {
        value += char;
      }
    };
    input.on("data", onData);
  });
}

async function askWithDefault(question, defaultValue) {
  const suffix = defaultValue ? ` [${defaultValue}]` : "";
  const answer = await ask(`${question}${suffix}: `);
  return answer || defaultValue || "";
}

async function pause(options, message) {
  if (options.yes) return;
  const seconds = Math.round(DEFAULT_PROMPT_TIMEOUT_MS / 1000);
  const { timedOut } = await askWithTimeout(
    `${message}\nPress Enter to continue (auto-continues in ${seconds}s)...`,
    DEFAULT_PROMPT_TIMEOUT_MS,
    "",
  );
  if (timedOut) console.log("Continuing automatically.");
}

async function confirm(options, message, defaultYes = false) {
  if (options.yes) return true;
  const suffix = defaultYes ? "[Y/n]" : "[y/N]";
  const seconds = Math.round(DEFAULT_PROMPT_TIMEOUT_MS / 1000);
  const defaultAnswer = defaultYes ? "y" : "n";
  const { answer: rawAnswer, timedOut } = await askWithTimeout(
    `${message} ${suffix} (auto-${defaultYes ? "yes" : "no"} in ${seconds}s) `,
    DEFAULT_PROMPT_TIMEOUT_MS,
    defaultAnswer,
  );
  if (timedOut) console.log(`Auto-selected ${defaultYes ? "yes" : "no"}.`);
  const answer = rawAnswer.toLowerCase();
  if (!answer) return defaultYes;
  return ["y", "yes"].includes(answer);
}

function validateWifiInput(ssid, password) {
  if (!ssid) throw new Error("SSID is required.");
  if (Buffer.byteLength(ssid, "utf8") > 32) throw new Error("SSID must be 32 bytes or less.");
  if (password && Buffer.byteLength(password, "utf8") > 63) throw new Error("WPA/WPA2 password must be 63 bytes or less.");
}

function normalizeSsidForMatch(ssid) {
  return ssid.toLowerCase().replace(/[-_\s]+/g, "");
}

function similarSsids(ssid, visibleSsids) {
  const normalized = normalizeSsidForMatch(ssid);
  return visibleSsids.filter((candidate) => {
    const candidateNormalized = normalizeSsidForMatch(candidate);
    return candidateNormalized.includes(normalized) || normalized.includes(candidateNormalized);
  });
}

async function warnIfTargetSsidNotVisible(options, logger, ssid) {
  if (process.platform !== "darwin") return { visible: null, current: null, similar: [] };
  const current = currentWifiSsid();
  if (current === ssid) return { visible: true, current, similar: [] };

  console.error(`Checking whether target Wi-Fi SSID "${ssid}" is visible...`);
  const visibleSsids = await scanWifiSsidsAsync();
  const visible = visibleSsids.includes(ssid);
  const similar = similarSsids(ssid, visibleSsids);
  logger.write("target_wifi_scan", {
    target: ssid,
    current,
    visibleCount: visibleSsids.length,
    targetVisible: visible,
    similar,
  }, [ssid]);

  if (visibleSsids.length === 0 || visible) return { visible, current, similar };

  const preview = visibleSsids.slice(0, 12).join(", ");
  const details = similar.length > 0 ? ` Similar visible SSID(s): ${similar.join(", ")}.` : ` Visible SSIDs include: ${preview}.`;
  console.error(`SSID "${ssid}" was not visible in the current Wi-Fi scan.${details}`);
  console.error("This can be normal for a hidden network; use the Wi-Fi connection test to validate the name and password.");
  return { visible, current, similar };
}

async function runTargetWifiTest(options, logger, ssid, password) {
  if (process.platform !== "darwin") {
    console.error("Wi-Fi connection testing is currently implemented only for macOS.");
    return { ok: false, skipped: true };
  }
  if (!options.autoWifi) {
    console.error("Wi-Fi connection testing is disabled because --no-auto-wifi was passed.");
    return { ok: false, skipped: true };
  }

  console.log(`Testing Wi-Fi connection to "${ssid}"...`);
  const result = connectWifiNetwork(ssid, password);
  logger.write(
    "target_wifi_connect",
    {
      target: ssid,
      ok: result.ok,
      iface: result.iface,
      error: result.error,
    },
    [ssid, password],
  );

  if (!result.ok) {
    console.error(`Wi-Fi connection command failed: ${result.error}`);
    return { ok: false, error: result.error };
  }

  const status = await waitForWifiNetworkReady(ssid, 30000, logger);
  if (status.ok) {
    console.log(
      JSON.stringify(
        {
          ok: true,
          ssid: status.current || ssid,
          addresses: status.addresses,
          gateway: status.gateway,
        },
        null,
        2,
      ),
    );
  }
  return status;
}

async function runProbe(options, logger) {
  const ssid = currentWifiSsid();
  const gateway = defaultGateway();
  const targets = softApTargets(options);
  console.error(`Current Wi-Fi: ${ssid || "unknown"}`);
  console.error(`Default gateway: ${gateway || "unknown"}`);
  console.error(`Local IPv4: ${localIPv4Addresses().join(", ") || "none"}`);
  console.error(`SoftAP targets: ${targets.map(({ host, port }) => `${host}:${port}`).join(", ")}`);

  const attempts = buildProbeAttempts(targets);
  const { responses } = await sendUdpAttempts(attempts, options.timeoutMs, logger);
  if (responses.length === 0) {
    throw new Error("No SoftAP responses. Confirm the Mac is connected to Air conditioner_****** and retry with --target <gateway> if needed.");
  }
  return responses;
}

async function runSoftApScan(options, logger) {
  const ssid = currentWifiSsid();
  const gateway = defaultGateway();
  const addresses = localIPv4Addresses();
  const hosts = options.targets.length > 0 ? unique(options.targets) : unique(softApTargets(options).map(({ host }) => host));

  console.error(`Current Wi-Fi: ${ssid || "unknown"}`);
  console.error(`Default gateway: ${gateway || "unknown"}`);
  console.error(`Local IPv4: ${addresses.join(", ") || "none"}`);
  console.error(`SoftAP hosts: ${hosts.join(", ")}`);
  console.error(`Ports: ${options.ports.join(", ")}`);

  for (const host of hosts) {
    const ping = runText("ping", ["-c", "1", "-W", "1000", host]);
    const ok = /1 packets received|1 received|bytes from/i.test(ping);
    console.log(JSON.stringify({ kind: "icmp", host, ok }, null, 2));
    logger.write("icmp_probe", { host, ok, output: ping.slice(0, 500) });
  }

  const tcp = await scanTcpPorts(hosts, options.ports, Math.min(options.timeoutMs, 1000));
  for (const result of tcp) {
    if (result.status === "open") {
      console.log(JSON.stringify({ kind: "tcp", ...result }, null, 2));
    }
    logger.write("tcp_port_scan", result);
  }

  const attempts = buildUdpScanAttempts(hosts, options.ports);
  const { responses } = await sendUdpAttempts(attempts, Math.min(options.timeoutMs, 1500), logger);
  if (responses.length === 0) {
    console.error("No UDP responses during SoftAP scan.");
  }

  const arp = runText("arp", ["-a"]);
  const relevantArp = arp
    .split("\n")
    .filter((line) => hosts.some((host) => line.includes(`(${host})`)))
    .join("\n");
  if (relevantArp) console.log(relevantArp);
  logger.write("arp", { relevantArp });
}

async function runSoftApQuickCheck(options, logger) {
  const ssid = currentWifiSsid();
  const gateway = defaultGateway();
  const addresses = localIPv4Addresses();
  const hosts = unique(softApTargets(options).map(({ host }) => host));

  console.error(`Current Wi-Fi: ${ssid || "unknown"}`);
  console.error(`Default gateway: ${gateway || "unknown"}`);
  console.error(`Local IPv4: ${addresses.join(", ") || "none"}`);
  console.error(`SoftAP quick-check hosts: ${hosts.join(", ")}`);

  for (const host of hosts) {
    const ping = runText("ping", ["-c", "1", "-W", "1000", host]);
    const ok = /1 packets received|1 received|bytes from/i.test(ping);
    console.log(JSON.stringify({ kind: "icmp", host, ok }, null, 2));
    logger.write("icmp_probe", { host, ok, output: ping.slice(0, 500) });
  }

  const arp = runText("arp", ["-a"]);
  const relevantArp = arp
    .split("\n")
    .filter((line) => hosts.some((host) => line.includes(`(${host})`)))
    .join("\n");
  if (relevantArp) console.log(relevantArp);
  logger.write("arp", { relevantArp });
}

async function runTestWifi(options, logger) {
  const ssid = options.ssid || (await ask("Target Wi-Fi SSID: "));
  validateWifiInput(ssid, "");
  await warnIfTargetSsidNotVisible(options, logger, ssid);
  const password = options.password ?? (await askHidden("Target Wi-Fi password: "));
  validateWifiInput(ssid, password);

  const status = await runTargetWifiTest(options, logger, ssid, password);
  if (!status.ok) {
    throw new Error("Target Wi-Fi connection test failed.");
  }
  console.log("Target Wi-Fi connection test passed.");
}

async function runApList(options, logger) {
  const targets = softApTargets(options);
  console.error(`AP-list/pubkey targets: ${targets.map(({ host, port }) => `${host}:${port}`).join(", ")}`);
  const attempts = buildApListAttempts(targets);
  const { responses: udpResponses } = await sendUdpAttempts(attempts, Math.max(options.timeoutMs, 7000), logger);
  const tcpListResponses = await sendTcpJson(targets, "ap-list-json-tcp", apListJson(7000), options.timeoutMs, logger);
  const tcpPubKeyResponses = await sendTcpJson(targets, "pubkey-json-tcp", pubKeyJson(2000), options.timeoutMs, logger);
  const responses = [...udpResponses, ...tcpListResponses, ...tcpPubKeyResponses];
  if (responses.length === 0) {
    throw new Error("No AP-list/pubkey responses. This is still useful: the lower BroadLink AP framing likely needs more reverse engineering.");
  }
  return responses;
}

async function sendApConfig(options, logger, ssid, password) {
  const provisionJson = appApConfigJson(ssid, password, 4000);
  const targets = softApWriteTargets(options);
  const standardTargets = softApBroadcastTargets(options);
  const standardPayload = buildStandardBroadlinkSetupPayload(ssid, password, options.securityMode);
  const standardAttempts = standardTargets.map(({ host, port }) => ({
    name: `broadlink-standard-setup-security-${options.securityMode}`,
    host,
    port,
    payload: standardPayload,
  }));
  const attempts = buildProvisionAttempts(targets, provisionJson);
  console.error(`Sending standard BroadLink setup to: ${standardTargets.map(({ host, port }) => `${host}:${port}`).join(", ")}`);
  const { responses: standardResponses } = await sendUdpAttempts(
    standardAttempts,
    Math.max(options.timeoutMs, 7000),
    logger,
    [password, ssid],
  );
  const setupInfo = standardResponses.map((response) => parseStandardSetupResponse(Buffer.from(response.hex, "hex"))).find(Boolean);
  if (setupInfo) {
    console.log("Standard BroadLink setup response received:");
    console.log(JSON.stringify(redactedSetupInfo(setupInfo), null, 2));
    console.log("Skipping experimental AP config fallbacks because the standard setup packet was acknowledged.");
    return { responses: standardResponses, setupInfo };
  }
  if (standardResponses.length > 0) {
    console.log("Standard BroadLink setup received a response, but it could not be decoded. Skipping experimental fallbacks to avoid disturbing pairing.");
    return { responses: standardResponses, setupInfo: null };
  }
  console.error(`Sending experimental AP config attempts to: ${targets.map(({ host, port }) => `${host}:${port}`).join(", ")}`);
  const { responses: udpResponses } = await sendUdpAttempts(attempts, Math.max(options.timeoutMs, 7000), logger, [password]);
  const tcpResponses = await sendTcpJson(targets, "ap-config-json-tcp", provisionJson, options.timeoutMs, logger, [password]);
  return { responses: [...standardResponses, ...udpResponses, ...tcpResponses], setupInfo: null };
}

function printProvisionResult(authenticated) {
  const result = {
    devices: authenticated.map(({ device, auth, deviceIdUsed, state }) => ({
      name: device.name,
      host: device.host,
      mac: device.mac,
      key: auth.key.toString("hex"),
      device_id: deviceIdUsed,
      state_keys: Object.keys(state).sort(),
    })),
  };

  console.log("\nProvisioning result. Treat this JSON as private because it contains local device keys:");
  console.log(JSON.stringify(result, null, 2));
  console.log("\nAdd the device in Home Assistant with Local discovery or Manual setup.");
}

function printSetupResponseResult(setupInfo) {
  if (!setupInfo?.devkey) return;
  const result = {
    device_from_softap_setup_response: {
      name: setupInfo.mac ? `TCL AC ${setupInfo.mac.replace(/:/g, "").slice(-6)}` : "TCL AC",
      host: null,
      mac: setupInfo.mac,
      key: setupInfo.devkey,
      device_id: 1,
      ssid: setupInfo.ssid,
      rssi: setupInfo.rssi,
    },
  };

  console.log("\nSoftAP setup response. Treat this JSON as private because it contains the local device key:");
  console.log(JSON.stringify(result, null, 2));
  console.log("If the AC joins Wi-Fi, use Local discovery first; use Manual setup only after you know its LAN IP.");
}

function wizardSoftApOptions(options) {
  return {
    ...options,
    targets: options.targets.length > 0 ? options.targets : [DEFAULT_SOFTAP_GATEWAY],
    timeoutMs: Math.max(options.timeoutMs, 5000),
  };
}

async function runWizard(options, logger) {
  const softApOptions = wizardSoftApOptions(options);
  const softApPrefixes = softApLocalPrefixes(softApOptions);

  console.log(
    [
      "",
      "TCL Intelligent AC manual offline pairing",
      "",
      "This wizard does not rely on macOS Wi-Fi SSID scanning.",
      "Use one AC at a time, disable VPN, and use a 2.4 GHz target Wi-Fi network.",
      "Temporarily disable Auto-Join for normal saved networks so macOS does not leave the AC hotspot mid-flow.",
      "",
      "Steps:",
      "1. Put the AC into CF pairing mode.",
      "2. Wait until Air conditioner_****** or Air conditioner-****** appears.",
      "3. Connect this Mac to that AC hotspot manually.",
      "4. If macOS says there is no internet, keep the connection anyway.",
      "5. Return here and press Enter.",
      "",
    ].join("\n"),
  );

  await ask("Press Enter after this Mac is connected to the AC hotspot...");

  let localApIp = localAddressForPrefixes(softApPrefixes);
  if (!localApIp) {
    console.log("Waiting for a SoftAP-side IP address from the AC hotspot...");
    const softApStatus = await waitForSoftApConnection(softApOptions, logger, 60000);
    localApIp = softApStatus.localAddress || localAddressForPrefixes(softApPrefixes);
  }

  if (localApIp) {
    console.log(`SoftAP local address detected: ${localApIp}`);
  } else {
    throw new Error("SoftAP address was not detected. Not sending Wi-Fi config because the Mac appears to be on another network.");
  }

  const ssid = options.ssid || (await ask("Target home 2.4 GHz Wi-Fi SSID: "));
  validateWifiInput(ssid, "");
  const password = options.password ?? (await askHidden("Target Wi-Fi password: "));
  validateWifiInput(ssid, password);

  if (options.diagnostics) {
    await runSoftApScan(softApOptions, logger);

    try {
      await runProbe(softApOptions, logger);
    } catch (error) {
      console.error(`Probe did not get a SoftAP response: ${error.message}`);
    }

    try {
      await runApList(softApOptions, logger);
    } catch (error) {
      console.error(`AP-list/pubkey stage did not get a response: ${error.message}`);
    }
  } else {
    await runSoftApQuickCheck(softApOptions, logger);
  }

  if (options.dryRun) {
    console.log("Dry run complete. No AP config write attempts were sent.");
    return;
  }

  await ask(`Press Enter to send Wi-Fi config for "${ssid}" to the AC...`);

  const { responses, setupInfo } = await sendApConfig(softApOptions, logger, ssid, password);
  if (responses.length === 0) {
    console.error("No AP config response was received. The AC may still reboot/join Wi-Fi, but this is not confirmed.");
  }
  if (setupInfo) {
    printSetupResponseResult(setupInfo);
  }

  await ask(
    [
      "",
      "Wi-Fi config was sent.",
      `Reconnect this Mac to "${ssid}" or another LAN that can reach the AC.`,
      "Wait until the AC hotspot disappears, then press Enter to verify LAN discovery...",
    ].join("\n"),
  );

  console.error(`Polling LAN discovery for up to ${options.postWifiTimeoutSec}s...`);
  const authenticated = await discoverAuthenticatedLanDevices(options.postWifiTimeoutSec * 1000);
  if (authenticated.length === 0) {
    if (setupInfo) printSetupResponseResult(setupInfo);
    throw new Error("No authenticated TCL AC appeared on the LAN after provisioning.");
  }

  printProvisionResult(authenticated);
}

async function runProvision(options, logger) {
  const ssid = options.ssid || (await ask("Target 2.4 GHz Wi-Fi SSID: "));
  validateWifiInput(ssid, "");
  await warnIfTargetSsidNotVisible(options, logger, ssid);
  const password = options.password ?? (await askHidden("Target Wi-Fi password: "));
  validateWifiInput(ssid, password);

  console.log(
    [
      "",
      "Pairing checklist:",
      "1. Put exactly one AC into pairing mode.",
      "2. Put the AC into CF mode. On the tested TCL XA71I, press DISPLAY or ECO 6 times within 8 seconds.",
      "3. Connect this Mac to the AC hotspot named like Air conditioner_******.",
      "4. Disable VPN while testing.",
      "",
    ].join("\n"),
  );

  await pause(options, "Connect to the AC hotspot now.");
  try {
    await runProbe(options, logger);
  } catch (error) {
    console.error(`Probe did not get a SoftAP response: ${error.message}`);
    const continueAnyway = await confirm(
      options,
      "Continue with AP config attempts anyway? This can still be useful if the write command responds differently.",
    );
    if (!continueAnyway) {
      throw new Error("Provisioning stopped after SoftAP probe timeout.");
    }
  }

  try {
    await runApList(options, logger);
  } catch (error) {
    console.error(`AP-list/pubkey stage did not get a response: ${error.message}`);
  }

  if (options.dryRun) {
    console.log("Dry run complete. No AP config write attempts were sent.");
    return;
  }

  const { responses, setupInfo } = await sendApConfig(options, logger, ssid, password);

  if (responses.length === 0) {
    console.error("No AP config response was received. The AC may still reboot/join Wi-Fi, but this is not confirmed.");
  }
  if (setupInfo) printSetupResponseResult(setupInfo);

  await pause(
    options,
    [
      `Reconnect this Mac to the target Wi-Fi network "${ssid}".`,
      "Wait until the AC hotspot disappears or the AC display leaves CF mode.",
    ].join("\n"),
  );

  console.error(`Polling LAN discovery for up to ${options.postWifiTimeoutSec}s...`);
  const authenticated = await discoverAuthenticatedLanDevices(options.postWifiTimeoutSec * 1000);
  if (authenticated.length === 0) {
    if (setupInfo) printSetupResponseResult(setupInfo);
    throw new Error("No authenticated TCL AC appeared on the LAN after provisioning.");
  }

  printProvisionResult(authenticated);
}

try {
  const options = parseArgs(process.argv.slice(2));
  if (options.help) {
    usage();
    process.exit(options.usageError ? 2 : 0);
  }

  const logger = createLogger(options);
  if (logger.path) console.error(`Debug log: ${logger.path}`);

  if (options.command === "probe") await runProbe(options, logger);
  else if (options.command === "softap-scan") await runSoftApScan(options, logger);
  else if (options.command === "ap-list") await runApList(options, logger);
  else if (options.command === "test-wifi") await runTestWifi(options, logger);
  else if (options.command === "wizard") await runWizard(options, logger);
  else if (options.command === "provision") await runProvision(options, logger);
} catch (error) {
  console.error(`Error: ${error.message}`);
  process.exit(1);
}
