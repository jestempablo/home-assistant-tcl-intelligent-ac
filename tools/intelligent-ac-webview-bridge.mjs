#!/usr/bin/env node

import { execFileSync } from "node:child_process";

const PACKAGE_NAME = "com.ab.smartDevice";
const DEVTOOLS_PORT = process.env.WEBVIEW_DEVTOOLS_PORT || "9222";

function usage() {
  console.error(
    [
      "Usage:",
      "  node tools/intelligent-ac-webview-bridge.mjs <deviceId> get",
      "  node tools/intelligent-ac-webview-bridge.mjs <deviceId> set <param> <value>",
      "",
      "Examples:",
      "  node tools/intelligent-ac-webview-bridge.mjs 000... get",
      "  node tools/intelligent-ac-webview-bridge.mjs 000... set temp 230",
      "  node tools/intelligent-ac-webview-bridge.mjs 000... set pwr 1",
    ].join("\n"),
  );
}

function adb(args, options = {}) {
  return execFileSync("adb", args, {
    encoding: "utf8",
    stdio: ["ignore", "pipe", options.stderr || "pipe"],
  }).trim();
}

function parseValue(raw) {
  if (raw === "true") return true;
  if (raw === "false") return false;
  if (raw === "null") return null;
  if (/^-?\d+(\.\d+)?$/.test(raw)) return Number(raw);
  return raw;
}

async function connectCdp(wsUrl) {
  const ws = new WebSocket(wsUrl);
  const pending = new Map();
  let id = 0;

  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    if (msg.id && pending.has(msg.id)) {
      pending.get(msg.id)(msg);
      pending.delete(msg.id);
    }
  };

  await new Promise((resolve, reject) => {
    ws.onopen = resolve;
    ws.onerror = reject;
  });

  return {
    close: () => ws.close(),
    call(method, params = {}) {
      return new Promise((resolve) => {
        const callId = ++id;
        pending.set(callId, resolve);
        ws.send(JSON.stringify({ id: callId, method, params }));
      });
    },
  };
}

function buildControlArgs(deviceId, action, param, rawValue) {
  const ctrl =
    action === "get"
      ? { act: "get", params: [], vals: [] }
      : {
          act: "set",
          params: [param],
          vals: [[{ val: parseValue(rawValue), idx: 1 }]],
        };

  return [
    deviceId,
    null,
    ctrl,
    "dev_ctrl",
    {
      localTimeout: 3000,
      remoteTimeout: 5000,
      sendCount: action === "set" ? 3 : 1,
    },
  ];
}

function mapState(result) {
  if (!result?.data?.params || !result?.data?.vals) return null;

  return Object.fromEntries(
    result.data.params.map((param, index) => [
      param,
      result.data.vals[index]?.[0]?.val,
    ]),
  );
}

const [deviceId, action, param, rawValue] = process.argv.slice(2);

if (!deviceId || !["get", "set"].includes(action)) {
  usage();
  process.exit(2);
}

if (action === "set" && (!param || rawValue === undefined)) {
  usage();
  process.exit(2);
}

const pid = adb(["shell", "pidof", PACKAGE_NAME]);
if (!pid) {
  throw new Error(`App ${PACKAGE_NAME} is not running. Open Intelligent AC first.`);
}

try {
  adb(["forward", "--remove", `tcp:${DEVTOOLS_PORT}`], { stderr: "ignore" });
} catch {
  // The forward may not exist yet.
}

adb(["forward", `tcp:${DEVTOOLS_PORT}`, `localabstract:webview_devtools_remote_${pid}`], {
  stderr: "ignore",
});

const pages = await (await fetch(`http://127.0.0.1:${DEVTOOLS_PORT}/json`)).json();
const page = pages.find((entry) => entry.type === "page" && entry.webSocketDebuggerUrl);
if (!page) {
  throw new Error("No debuggable WebView page found.");
}

const cdp = await connectCdp(page.webSocketDebuggerUrl);

try {
  await cdp.call("Runtime.enable");

  const bridgeArgs = buildControlArgs(deviceId, action, param, rawValue);
  const expression = `(async()=>JSON.stringify(await new Promise(resolve=>cordova.exec(r=>resolve({ok:true,result:r}),e=>resolve({ok:false,error:e}),"BLNativeBridge","devicecontrol",${JSON.stringify(
    bridgeArgs,
  )}))))()`;

  const response = await cdp.call("Runtime.evaluate", {
    expression,
    awaitPromise: true,
    returnByValue: true,
  });

  const bridgeResponse = JSON.parse(response.result.result.value);
  if (!bridgeResponse.ok) {
    console.log(JSON.stringify(bridgeResponse, null, 2));
    process.exitCode = 1;
  } else {
    const result = JSON.parse(bridgeResponse.result);
    const state = mapState(result);
    console.log(JSON.stringify({ result, state }, null, 2));
  }
} finally {
  cdp.close();
}
