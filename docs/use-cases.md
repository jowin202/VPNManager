# Use cases

The VPN Manager routes *devices*, not apps. Everything a device does goes
through the VPN assigned to it. Some ideas:

## Streaming from another country

Assign the **smart TV** or streaming stick to a VPN in country A to see the
catalogue of that country. Other devices keep using your normal connection, so
video calls, online banking and gaming are not affected.

> Streaming services may restrict VPN usage in their terms of service. Check
> them before you use this.

## Internet radio and podcasts with regional restrictions

Some radio stations only stream inside their country. Put the **internet radio**
or smart speaker on a VPN in country B – independently of the TV in country A.

## Travelling with your home network

Living abroad for a while? Route the TV through a VPN back home and use your
local services as usual, while the rest of the household uses the local
connection.

## Privacy for the whole device

Route a **tablet, phone or laptop** through a VPN without installing a VPN app –
useful for devices where apps are not possible (e-book readers, consoles,
smart-home gadgets). With Proton's *NetShield* option in the config, ads and
trackers are blocked via DNS, provided the device uses the Proton DNS server.

## Gaming in another region

Assign a **game console** to a VPN close to the game servers of another region,
e.g. to play with friends abroad.

## Keep some devices out of the VPN

A **work laptop** with its own company VPN, video conferencing or online banking
often works best without an additional VPN. Simply leave these devices on
*No VPN*.

## Testing websites from different countries

Developers and marketers can check geo-dependent content (prices, language,
cookie banners) by moving a test machine between VPNs in a few clicks.

## Example household

| Device | VPN | Purpose |
|---|---|---|
| Living-room TV | `tv_usa` (Proton, USA) | US streaming catalogue |
| Kitchen radio | `radio_uk` (Proton, UK) | UK radio stations |
| Kids' tablet | `nl_netshield` (Proton, Netherlands, NetShield on) | Fewer ads and trackers |
| Game console | `tv_usa` | US game servers |
| Work laptop | No VPN | Company VPN, video calls |
| Phones | No VPN | Normal use |
