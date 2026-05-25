# TCL Intelligent AC Reverse Engineering Notes

## Scope

Tested with TCL XA71I devices using the Intelligent AC Android app. The goal is local interoperability for owned devices.

## Android app path

The new app uses BroadLink SDK / DNA local control:

```text
Cordova WebView
  -> BLNativeBridge.deviceControl(...)
  -> BLPluginInterfacer.ControlTask
  -> BLLet.Controller.dnaControl(...)
  -> cn.com.broadlink.networkapi.NetworkAPI.dnaControl(...)
  -> libNetworkAPI.so
```

The app normally gets device metadata from the BroadLink cloud, including MAC, local IP, password/id, and per-device AES key. Friendly names are displayed from `familyallinfo[].moduleinfo[].name`, mapped back to `devinfo` by `moduleinfo[].moduledev[].did`. The tested TCL XA71I units also return the local AES key through standard BroadLink LAN authentication, so already-paired devices can be bootstrapped without an Intelligent AC account.

## Local transport

- Protocol: UDP
- Port: `80`
- Discovery also works over BroadLink-style UDP; both tested ACs responded from their LAN IPs.
- Tested discovery response fields:
  - `devtype`: `0x507c`
  - `name`: `OEM_TCLISO_<mac suffix>`
  - `mac`: at response offset `0x3a..0x3f`, reversed
- Control packets use BroadLink/DNA framing with magic `5aa5aa555aa5aa55`.

Outer packet:

| Offset | Size | Meaning |
| --- | ---: | --- |
| `0x00` | 8 | magic `5aa5aa555aa5aa55` |
| `0x20` | 2 | packet checksum, little endian |
| `0x24` | 2 | device type `0x507c`, little endian |
| `0x26` | 2 | command `0x006a`, response `0x03ee` |
| `0x28` | 2 | request nonce copied into response |
| `0x2a` | 6 | reversed device MAC |
| `0x30` | 4 | device id, observed `1` |
| `0x34` | 2 | plaintext payload checksum |
| `0x38` | n | AES-CBC encrypted inner payload |

Checksums start at `0xbeaf` and add all bytes modulo `0x10000`.

## Encryption

- AES-128-CBC
- No padding at crypto layer; inner plaintext is zero-padded to a 16-byte AES block boundary
- IV: `562e17996d093d28ddb3ba695a2e6f58`
- Initial BroadLink auth key: `097628343fe99e23765c1513accf8b02`
- Runtime key: per-device AES key from BroadLink LAN auth or app device metadata

## LAN authentication

Standard BroadLink auth works against the tested ACs:

- command: `0x0065`
- auth payload length: `0x50`
- response payload decrypts with the initial BroadLink key
- decrypted response bytes `0x00..0x03`: device id, observed `1`
- decrypted response bytes `0x04..0x13`: per-device AES key for local TCL/DNA commands

After auth, a normal TCL `get` command succeeds using the returned key and device id.

## Inner payload

Inner payload starts with:

| Offset | Size | Meaning |
| --- | ---: | --- |
| `0x00` | 2 | meaningful length excluding these two bytes |
| `0x02` | 4 | inner magic `a5a55a5a` |
| `0x06` | 2 | inner checksum over bytes `0x02..0x05` plus `0x08..end` |
| `0x08` | 1 | action: `1` get, `2` set |
| `0x09` | 1 | constant `0x0b` |
| `0x0a` | 4 | JSON body length, little endian |
| `0x0e` | n | JSON body |

Examples after decrypt:

```text
get:
0e00 a5a55a5a b3c1 010b 02000000 7b7d
body: {}

set temp 23.0 C:
1800 a5a55a5a 87c4 020b 0c000000 7b2274656d70223a3233307d 000000000000
body: {"temp":230}
```

## Confirmed parameters

- `pwr`: `0` off, `1` on
- `temp`: target temperature in tenths of Celsius, e.g. `230`
- `envtemp`: current room temperature in Celsius
- `tcl_mode`: `1` heat, `2` dry, `3` cool, `4` fan, `5` auto
- `tcl_mark`: `0` auto, `1` low, `2` medium, `3` high, `4` mid low, `5` mid high
- `tcl_vdir`: vertical swing code
- `tcl_hdir`: horizontal swing code

## Local test client

```bash
node tools/tcl-ac-local.mjs example get
node tools/tcl-ac-local.mjs example set temp 230
node tools/tcl-ac-local.mjs example set pwr 1
node tools/tcl-ac-local.mjs --host 192.168.1.50 --mac aa:bb:cc:dd:ee:ff --key 00000000000000000000000000000000 get
node tools/broadlink-discover.mjs 192.168.1.50
node tools/broadlink-auth-test.mjs 192.168.1.50
```
