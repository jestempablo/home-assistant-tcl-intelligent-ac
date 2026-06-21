# TCL Intelligent AC for Home Assistant

Unofficial Home Assistant custom integration for air conditioners controlled by the **Intelligent AC** mobile app.

This integration was built to control owned TCL air conditioners locally from Home Assistant. For the tested TCL XA71I, the official app is not required for normal Home Assistant use: the included CLI can pair the AC to Wi-Fi without an Intelligent AC account, and Home Assistant can then discover and authenticate it over the LAN. Cloud-assisted setup is still available as a fallback to fetch LAN details from the Intelligent AC cloud. Runtime control is local over the LAN.

## Current status

Tested hardware:

- TCL XA71I split AC
- Intelligent AC Android app 1.0.12
- Region: United States / Other
- Local UDP control over BroadLink/DNA framing
- Accountless local Wi-Fi pairing through `tools/tcl-ac-provision.mjs`

Not tested:

- Other TCL models
- Other OEM brands using the same BroadLink/DNA platform
- Europe, China, and Russia account regions
- Devices added through another branded app

Maintenance note: this project is maintained on a best-effort basis. Issues and pull requests are welcome, especially for additional models and regions, but support for untested hardware cannot be promised.

## Important app and region notes

For the tested TCL XA71I local setup path, the official **Intelligent AC** app is no longer required after installation. Use `tools/tcl-ac-provision.mjs` to pair an unconfigured AC to Wi-Fi, then use **Local discovery (recommended)** in Home Assistant. The official app is still useful as a fallback for cloud-assisted setup and for native schedule/reservation features, which this integration intentionally does not implement.

For cloud-assisted setup, create and configure your device in the official **Intelligent AC** app first. LAN discovery setup can add already-paired devices without logging in to Intelligent AC.

During testing, the working region was **United States / Other**. The **Europe** region may let the app setup continue without a clear error, but device pairing did not work in testing. Switching the same setup to **United States / Other** made pairing and Home Assistant setup work. Older Intelligent AC manuals still document a European server, but this project has not found a working EU setup path. China and Russia are present in the app but have not been tested.

If the app asks for an activation code, choose **Enter manually** and enter:

```text
tclkt
```

## Installation with HACS

This repository is intended to work as a HACS custom repository.

1. In Home Assistant, open HACS.
2. Open **Custom repositories**.
3. Add this GitHub repository URL.
4. Select category **Integration**.
5. Install **TCL Intelligent AC**.
6. Restart Home Assistant.
7. Go to **Settings > Devices & services > Add integration**.
8. Search for **TCL Intelligent AC**.

## Manual installation

Copy this directory:

```text
custom_components/tcl_intelligent_ac
```

to:

```text
<your Home Assistant config>/custom_components/tcl_intelligent_ac
```

Then restart Home Assistant and add **TCL Intelligent AC** from **Settings > Devices & services**.

## Configuration

Automatic discovery:

Home Assistant can discover supported already-paired devices from DHCP information when their MAC address or hostname matches the tested TCL/BroadLink profile. The discovered flow still validates the device locally with BroadLink authentication before showing it for setup. If DHCP discovery does not appear, use **Local discovery (recommended)** from the normal add-integration flow.

Discovery hides devices that are already configured. If an already-configured device is found at a new IP address, the integration updates the stored host instead of offering a duplicate setup.

Recommended setup path for new devices without the app:

1. Install this repository on a Mac or laptop with Node.js available.
2. Put the AC in pairing mode and run `node tools/tcl-ac-provision.mjs wizard --debug`.
3. Follow the CLI prompts to connect the laptop to `Air conditioner_******` or `Air conditioner-******`, enter the target Wi-Fi, and send the config.
4. Reconnect the laptop to the target Wi-Fi and let the CLI verify LAN discovery/auth.
5. In Home Assistant, select **Local discovery (recommended)** and add the discovered AC.

Recommended setup path for already-paired LAN devices:

1. Select **Local discovery (recommended)**.
2. Leave the optional discovery fields unchanged, or enter known device IP addresses in **Known IP addresses** if automatic discovery misses a device.
3. Select the discovered AC devices.

This sends local BroadLink/DNA discovery and authentication packets on your LAN. It does not use Intelligent AC credentials. It only works for devices that are already connected to the same network as Home Assistant.

Cloud-assisted setup path:

1. Select **Cloud-assisted setup**.
2. Enter the Intelligent AC account email/phone and password.
3. Select the same region used in the app.
4. Select the discovered AC devices.

The account password is used once during setup and is not stored by the integration. The integration stores the local device details needed for LAN control, including the per-device key.
Cloud-assisted setup uses the friendly names stored in the Intelligent AC family metadata when they are available.

Manual setup is also available if you already know the device LAN IP, MAC address, and local key.

## Pairing without the app

This repository includes a CLI for local Wi-Fi pairing without the Intelligent AC mobile app:

```text
node tools/tcl-ac-provision.mjs test-wifi --ssid "Your 2.4 GHz Wi-Fi" --debug
node tools/tcl-ac-provision.mjs wizard --debug
node tools/tcl-ac-provision.mjs probe --debug
node tools/tcl-ac-provision.mjs softap-scan --debug
node tools/tcl-ac-provision.mjs ap-list --debug
node tools/tcl-ac-provision.mjs provision --ssid "Your 2.4 GHz Wi-Fi" --debug
```

The tool is not part of the Home Assistant integration runtime and does not add devices to the Intelligent AC cloud. It is intended to run from a Mac or laptop while connecting that computer to the AC hotspot named like `Air conditioner_******` or `Air conditioner-******`.

Omit `--password` to use the hidden password prompt. Passing Wi-Fi passwords directly on the command line can expose them to local process listings.

Recommended first test flow:

1. Make sure the target Wi-Fi is 2.4 GHz.
2. Disable VPN on the laptop. On macOS, temporarily disable Auto-Join for all normal saved networks, or temporarily forget them. This matters because the AC can drop SoftAP setup when the setup client disconnects.
3. Put the AC in `CF` pairing mode. On the tested TCL XA71I, pressing `DISPLAY` or `ECO` six times within eight seconds enters `CF`; other models may use a different gesture.
4. Wait for the hotspot to appear. `CF` can disappear before `Air conditioner_******` or `Air conditioner-******` becomes visible; this is normal on the tested unit.
5. Connect the laptop to `Air conditioner_******` or `Air conditioner-******` manually. If macOS says there is no internet, keep the connection anyway.
6. Run `wizard`, press Enter, then enter the target Wi-Fi SSID and password.
7. Press Enter to send the Wi-Fi config to the AC.
8. Reconnect the laptop to the target Wi-Fi, press Enter again, and let the wizard verify the AC with LAN discovery/auth.

Use `--dry-run` with `wizard` or `provision` to stop before sending Wi-Fi credentials. The lower-level `probe`, `softap-scan`, and `ap-list` commands are useful when the wizard cannot identify the SoftAP behavior; pass `--diagnostics` to run those extra probes during the wizard before sending Wi-Fi config. The wizard intentionally does not rely on macOS SSID scanning or automatic Wi-Fi switching; it verifies the SoftAP connection by checking for a local SoftAP address (`192.168.10.x` on the tested unit). After sending Wi-Fi credentials, it waits for you to reconnect to LAN and then polls local discovery/auth for up to `--post-wifi-timeout` seconds. The default BroadLink setup security mode is `3` (WPA2); use `--security-mode 4` only if testing a mixed WPA/WPA2 network. Debug logs are written to `logs/tcl-ac-provision-*.jsonl` and redact passwords and keys, but still treat them as private because they can include MAC addresses, local IPs, and packet captures. Do not publish full unredacted logs.

If provisioning succeeds, add the device through **Local discovery** in Home Assistant. On the tested TCL XA71I, this is enough to use the AC locally without the official app. If another firmware requires cloud-signed data, the tool may only produce diagnostics; in that case pair with the official app and use the normal local setup path.

## Entities

Each configured AC exposes:

- climate entity for power, HVAC mode, target temperature, fan speed, and swing
- switches for Evaporator clean, Turbo, Eco, Quiet, Display, Buzzer, Anti-mildew, Health, and Frost protection when the device reports those parameters
- Sleep select with off, normal, senior, child, and custom modes
- diagnostic sensors for outdoor temperature, coil temperature, vent temperature, error codes, filter dirty, and clean check

Timer/reservation is intentionally not exposed. The APK maps those features to scheduling fields, not to one simple local toggle. Home Assistant automations cover most timer use cases more safely; keep the official app only if you specifically need its native scheduling UI.

## Known limitations

- Cloud-assisted setup is confirmed only for the United States / Other region.
- EU setup may fail during device pairing without a clear app error; use United States / Other unless you have confirmed another region works for your device.
- LAN discovery setup requires the device to already be paired to Wi-Fi and reachable from Home Assistant. Use the included pairing CLI for accountless local pairing on tested devices.
- Some devices can report `LOCKED=True` in BroadLink/DNA discovery. In that state, local authentication can fail with `0xffff` until the lock is cleared.
- LAN discovery may require entering known device IP addresses if broadcast or subnet scanning is blocked by your network.
- Pairing without the app is handled by a separate CLI tool, not by the Home Assistant config flow.
- Other brands and models may work if they use the same BroadLink/DNA AC profile, but they are not tested.
- Cloud control is not implemented. Cloud is used only to bootstrap local control.
- Device keys, MAC addresses, and logs should be treated as private if you share diagnostics publicly.
- HACS and Home Assistant may cache brand images. If the icon does not show immediately after installation, restart Home Assistant and refresh the browser or mobile app.

## Troubleshooting

`invalid_auth`: the cloud login rejected the credentials or the selected region is wrong.

`cloud_error`: cloud setup failed before devices could be fetched. Check Home Assistant logs for the detailed response.

`no_lan_devices`: LAN discovery did not find any supported devices or could not authenticate them. Try entering known device IP addresses, make sure Home Assistant is on the same network, and check that UDP traffic to port 80 is allowed.

`locked_device`: LAN discovery found a supported AC, but the device reported `LOCKED=True` and blocked BroadLink/DNA local authentication. This can also appear in diagnostics as authentication error `0xffff`. If a BroadLink-style app exposes **Lock device**, disable it. If Intelligent AC does not expose that setting, a deeper Wi-Fi module or factory-style reset may be required. In issue #1, removing and re-adding the AC in Intelligent AC did not clear the flag, but a deeper reset changed discovery from `LOCKED=True` to `LOCKED=False`, after which Local discovery worked normally.

`host_not_found`: the device was found in the account, but Home Assistant could not resolve or reach it on the LAN. Make sure Home Assistant and the AC are on the same network and that UDP traffic is not blocked.

`cannot_connect`: the integration could not talk to the device with the resolved or manually entered LAN details.

Known cloud setup error:

`/ec4/v1/family/getallinfo failed: 不是有效的申请 (-30103)` was reported by a user whose local `LOCKED=True` issue was later solved by a device reset. Treat this as a separate cloud bootstrap failure. It may mean the cloud endpoint rejects the request context, account type, app region, or shared-device account. Local discovery can still work once the device is unlocked.

## Reverse-engineering notes

The tested devices use local UDP on port 80 with BroadLink/DNA framing:

- AES-128-CBC
- per-device key from BroadLink/DNA local authentication or Intelligent AC device metadata
- JSON command bodies for get/set operations

Confirmed parameters include:

- `pwr`: power
- `temp`: target temperature in tenths of Celsius
- `envtemp`: room temperature
- `tcl_mode`: heat, dry, cool, fan, auto
- `tcl_mark`: fan speed
- `tcl_vdir` / `tcl_hdir`: vertical/horizontal swing
- `pwfmode`: turbo / powerful
- `qtmode`: quiet
- `ecomode`: eco
- `tcl_slp`: sleep mode
- `bglight`: display / background light
- `beep`: buzzer
- `smartdesic`: anti-mildew
- `evaportor`: evaporator clean
- `ac_health`: health mode
- `8heat`: 8C heat / frost protection

More detailed local notes are in `docs/reverse-engineering-notes.md`.

## Contributing

Useful reports include:

- AC brand and exact model
- Intelligent AC region
- Home Assistant version
- integration version
- whether setup was LAN-discovered, cloud-assisted, or manual
- redacted Home Assistant logs
- redacted state payloads if you are adding model support

Do not post account passwords, session tokens, per-device keys, or full unredacted packet captures in public issues.
