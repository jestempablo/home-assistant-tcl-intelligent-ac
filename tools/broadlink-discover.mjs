#!/usr/bin/env node
import dgram from "node:dgram";

const DISCOVERY_PORT = Number(process.env.BL_DISCOVERY_PORT || 36200);
const DURATION_MS = Number(process.env.BL_DISCOVERY_MS || 5000);

// 48-byte BroadLink discovery payload observed from the Intelligent AC SDK.
// It includes the SDK magic/header and is sufficient for legacy BroadLink
// devices to answer; local IP/clock fields do not appear to be enforced.
const payload = Buffer.from(
  "5aa5aa555aa5aa5502000000ea07041f0a001805000000001002000a688d0000ffc40000000006000000000000000000",
  "hex",
);

function reverseMac(buf) {
  return Array.from(buf).reverse().map((b) => b.toString(16).padStart(2, "0")).join(":");
}

function parseResponse(msg, rinfo) {
  const hex = msg.toString("hex");
  const ascii = msg.toString("latin1").replace(/[^\x20-\x7e]+/g, " ").trim();

  const candidates = [];
  for (let offset = 0; offset <= msg.length - 6; offset += 1) {
    const slice = msg.subarray(offset, offset + 6);
    const mac = reverseMac(slice);
    if (/^(24:df:a7|34:ea:34|ec:0b:ae|b4:43:0d|a0:43:b0)/i.test(mac)) {
      candidates.push({ offset, mac });
    }
  }

  return {
    from: `${rinfo.address}:${rinfo.port}`,
    length: msg.length,
    macs: candidates,
    ascii,
    hex,
  };
}

const socket = dgram.createSocket({ type: "udp4", reuseAddr: true });
const seen = new Set();

socket.on("message", (msg, rinfo) => {
  const key = `${rinfo.address}:${rinfo.port}:${msg.toString("hex")}`;
  if (seen.has(key)) return;
  seen.add(key);
  console.log(JSON.stringify(parseResponse(msg, rinfo), null, 2));
});

socket.on("listening", () => {
  socket.setBroadcast(true);
  const targets = [
    ["255.255.255.255", 80],
    ["224.0.0.251", 80],
    ["224.0.0.251", 16680],
  ];

  for (const [host, port] of targets) {
    socket.send(payload, port, host);
  }

  console.error(`sent discovery from UDP/${DISCOVERY_PORT}, waiting ${DURATION_MS}ms`);
});

socket.bind(DISCOVERY_PORT, "0.0.0.0");

setTimeout(() => {
  socket.close();
}, DURATION_MS);
