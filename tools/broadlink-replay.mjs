#!/usr/bin/env node
import dgram from "node:dgram";

const [host, payloadHex] = process.argv.slice(2);
if (!host || !payloadHex) {
  console.error("usage: node tools/broadlink-replay.mjs <host> <udp-payload-hex>");
  process.exit(2);
}

const payload = Buffer.from(payloadHex.replace(/\s+/g, ""), "hex");
const socket = dgram.createSocket("udp4");
let done = false;

function closeSocket() {
  if (done) return;
  done = true;
  clearTimeout(timeout);
  socket.close();
}

socket.on("message", (msg, rinfo) => {
  console.log(JSON.stringify({
    from: `${rinfo.address}:${rinfo.port}`,
    length: msg.length,
    hex: msg.toString("hex"),
  }, null, 2));
  closeSocket();
});

socket.on("listening", () => {
  const address = socket.address();
  console.error(`sending ${payload.length} bytes from UDP/${address.port} to ${host}:80`);
  socket.send(payload, 80, host);
});

const timeout = setTimeout(() => {
  console.error("timeout");
  closeSocket();
}, Number(process.env.BL_REPLAY_TIMEOUT_MS || 3000));

socket.bind(0, "0.0.0.0");
