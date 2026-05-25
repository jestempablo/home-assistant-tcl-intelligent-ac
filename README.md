# TCL Intelligent AC for Home Assistant

Unofficial Home Assistant custom integration for air conditioners controlled by the **Intelligent AC** mobile app.

This integration was built to control owned TCL air conditioners locally from Home Assistant. It can discover and authenticate already-paired devices over the LAN without using an Intelligent AC account. Cloud-assisted setup is still available as a fallback to fetch the LAN details from the Intelligent AC cloud. Runtime control is local over the LAN.

## Current status

Tested hardware:

- TCL XA71I split AC
- Intelligent AC Android app 1.0.12
- Region: United States / Other
- Local UDP control over BroadLink/DNA framing

Not tested:

- Other TCL models
- Other OEM brands using the same BroadLink/DNA platform
- Europe, China, and Russia account regions
- Devices added through another branded app

Maintenance note: this project is maintained on a best-effort basis. Issues and pull requests are welcome, especially for additional models and regions, but support for untested hardware cannot be promised.

## Important region and app notes

For cloud-assisted setup, create and configure your device in the official **Intelligent AC** app first. LAN discovery setup can add already-paired devices without logging in to Intelligent AC.

During testing, the working region was **United States / Other**. The **Europe** region appeared unavailable or disabled in the app setup flow and is not known to work here. China and Russia are present in the app but have not been tested.

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

## Entities

Each configured AC exposes:

- climate entity for power, HVAC mode, target temperature, fan speed, and swing
- switches for Evaporator clean, Turbo, Eco, Quiet, Display, Buzzer, Anti-mildew, Health, and Frost protection when the device reports those parameters
- Sleep select with off, normal, senior, child, and custom modes
- diagnostic sensors for outdoor temperature, coil temperature, vent temperature, error codes, filter dirty, and clean check

Timer/reservation is intentionally not exposed yet. The APK maps those features to scheduling fields, not to one simple local toggle. Home Assistant automations cover most timer use cases more safely.

## Known limitations

- Cloud-assisted setup is confirmed only for the United States / Other region.
- EU setup is not known to work.
- LAN discovery setup requires the device to already be paired to Wi-Fi and reachable from Home Assistant.
- LAN discovery may require entering known device IP addresses if broadcast or subnet scanning is blocked by your network.
- Other brands and models may work if they use the same BroadLink/DNA AC profile, but they are not tested.
- Cloud control is not implemented. Cloud is used only to bootstrap local control.
- Device keys, MAC addresses, and logs should be treated as private if you share diagnostics publicly.
- HACS and Home Assistant may cache brand images. If the icon does not show immediately after installation, restart Home Assistant and refresh the browser or mobile app.

## Troubleshooting

`invalid_auth`: the cloud login rejected the credentials or the selected region is wrong.

`cloud_error`: cloud setup failed before devices could be fetched. Check Home Assistant logs for the detailed response.

`no_lan_devices`: LAN discovery did not find any supported devices or could not authenticate them. Try entering known device IP addresses, make sure Home Assistant is on the same network, and check that UDP traffic to port 80 is allowed.

`host_not_found`: the device was found in the account, but Home Assistant could not resolve or reach it on the LAN. Make sure Home Assistant and the AC are on the same network and that UDP traffic is not blocked.

`cannot_connect`: the integration could not talk to the device with the resolved or manually entered LAN details.

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
