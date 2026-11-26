# Wake-on-LAN on an ASRock Rack X570D4U-2L2T

How to set up an ASRock Rack **X570D4U-2L2T** backup box running TrueNAS SCALE **25.10** or later so the container can wake it with Wake-on-LAN (`wol`). The general steps are in [Setup](setup.md#wake-on-lan-optional); this page fills in the details for this board.

Written against the board's [user manual](https://download.asrock.com/Manual/X570D4U-2L2T.pdf) (version 1.0, September 2022); page numbers below are the manual's. Written for BIOS **2.22.1282** and BMC **1.35.00**; the manual predates them, so if a setting is not where this page says, look for the same name nearby.

This board also has a BMC, so it can be powered on with **IPMI** too. See [IPMI or Wake-on-LAN?](#ipmi-or-wake-on-lan) at the end.

## Which port to use: LAN3 or LAN4 (10GbE)

The rear panel (manual p.12) has four host ports and one BMC port:

| Label | Speed | Controller | Linux driver | Wakes from off (S5)? |
|-------|-------|------------|--------------|----------------------|
| LAN1, LAN2 | 1GbE | Intel i210 | `igb` | Not supported by ASRock. Don't use these for Wake-on-LAN. |
| **LAN3, LAN4** | 10GbE | Intel X550-AT2 | `ixgbe` | **Yes.** The manual (p.5): "onboard LAN3&4 can wake up S5". |
| IPMI_LAN1 | 1GbE | Realtek RTL8211E (BMC) | (none) | Not a host port: this is the BMC's own port, used by `ipmi`. |

So the magic packet has to arrive on **LAN3 or LAN4**, and that port has to be cabled to the same LAN (and VLAN) as the primary. The packet is a broadcast, so it only reaches ports on the primary's network segment.

**The switch port must accept a slow link.** While the box is off, the X550 drops its link to 1GbE or 100Mb to save power. A switch port, adapter or SFP+ RJ45 module that only does 10GbE has no link in that state, so the packet never arrives. If the LAN3/LAN4 link lights go dark when the box is off, that is the cause: use a switch port that also does 1G/100M.

## 1. BIOS

Reboot into the BIOS (press **F2** or **Del** at the ASRock Rack logo, or use the BMC web UI's remote console). Only three settings matter:

| Menu path | Setting | Set to | Why |
|-----------|---------|--------|-----|
| Advanced > ACPI Configuration (p.54) | **PCIE Devices Power On** | **Enabled** | "Allow the system to be waked up by a PCIE device and enable wake on LAN." This is the only wake switch on this board; there is no separate "Onboard LAN Power On", Deep S5 or ErP setting. |
| Advanced > Chipset Configuration (p.51-52) | **Onboard X550 LAN1**, **Onboard X550 LAN2** | **Enabled** | The X550's two ports, labelled LAN3 and LAN4 on the rear panel (the BIOS counts them from 1). Disabled means no port and no wake. |
| Advanced > Chipset Configuration (p.51) | **Restore AC Power Loss** | **Power Off** | The box stays off after a power cut and waits for the next run. |

Save and exit (**F10**). The **Boot From Onboard LAN** items on the Boot screen are network (PXE) boot, not wake: leave them as they are.

## 2. Find the interface and MAC

On the backup box: **System > Shell**.

```
for i in $(ls /sys/class/net); do printf '%-12s %-8s %s\n' "$i" "$(ethtool -i $i 2>/dev/null | awk '/driver/{print $2}')" "$(cat /sys/class/net/$i/address)"; done
```

lists every interface with its driver and MAC. The two `ixgbe` interfaces are LAN3 and LAN4. To tell which one is cabled:

```
ip -br link | grep -v DOWN
```

Use the `ixgbe` interface that is `UP`. Its MAC goes into `WOL_MAC` on the primary. (It is also shown in the web UI under **Network > Interfaces**.)

Then check that it can wake the box:

```
ethtool <interface> | grep -i wake
```

```
	Supports Wake-on: g
	Wake-on: d
```

`Supports Wake-on` must contain **g** (magic packet). `Wake-on: d` means it is off right now, which the next step fixes. If `Supports Wake-on` is only `d`, the card's firmware doesn't offer it: check that **PCIE Devices Power On** is enabled, then use IPMI instead.

## 3. Turn Wake-on-LAN on in TrueNAS, persistently

A setting made in the shell is lost at the next boot. So set it on every boot with an init script:

1. **System > Advanced Settings > Init/Shutdown Scripts > Add**.
2. **Description**: `Wake-on-LAN`. **Type**: Command. **Command**: `ethtool -s <interface> wol g`. **When**: Post Init. **Enabled**: on.
3. Add a second one, the same but **When**: Shutdown, so it is set again just before the box powers off.
4. Run the command once in the shell so it applies now, and check: `ethtool <interface> | grep Wake-on` shows `Wake-on: g`.

If the interface name ever changes (for example after adding a PCIe card), update both scripts.

## 4. Configure the container on the primary

In the container's `.env` on the **primary**:

```
WOL_MAC=aa:bb:cc:dd:ee:ff        # the LAN3/LAN4 MAC from step 2
WOL_BROADCAST=192.168.1.255      # your LAN's broadcast address
POWER_ON_ORDER=ipmi,wol          # or wol,ipmi, or just wol (see below)
```

Recreate the container so it picks up the change. The container must use `network_mode: host` (as in the compose example), or the broadcast never leaves Docker's network.

## 5. Test it

1. Shut the backup box down normally from its web UI (power icon > **Shut Down**) and wait until it is off.
2. Look at the LAN3/LAN4 port: its link light should still be on. If it is dark, see [the switch port](#which-port-to-use-lan3-or-lan4-10gbe).
3. On the primary (**System > Shell**), send a magic packet from the container, with your MAC (without colons) and broadcast:

   ```
   docker exec truenas-backup python -c "import socket; m = bytes.fromhex('aabbccddeeff'); s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1); s.sendto(b'\xff' * 6 + m * 16, ('192.168.1.255', 9))"
   ```

4. The box should power on within a few seconds.
5. Test the whole power-on path the way the schedule uses it, with only Wake-on-LAN: shut the box down again, set `POWER_ON_ORDER=wol` temporarily, recreate the container and run `docker exec truenas-backup backup --no-shutdown`. Put your real order back afterwards.

## If it doesn't wake

Work down this list; each step rules one thing out.

1. **Right port?** The cable must be in **LAN3 or LAN4** (10GbE). LAN1/LAN2 (i210) don't wake the box.
2. **Right MAC?** `WOL_MAC` must be the MAC of that same port (step 2), not of another port or of the BMC.
3. **Armed at shutdown?** Boot the box, run `ethtool <interface> | grep Wake-on`. It must say `g`. If it says `d`, the init script names the wrong interface or isn't enabled.
4. **Link while off?** Shut down and look at the port's link light, and at the switch's port status. No link: the switch port can't do 1G/100M (see above), or the port is disabled in the BIOS.
5. **Does the packet arrive?** Boot the box and, on it, run `tcpdump -i <interface> -n 'udp port 9'`. Send the packet from the primary (step 5.3). Nothing shown: the broadcast address or VLAN is wrong, or the container is not on the host network.
6. **BIOS:** **PCIE Devices Power On** enabled, **Onboard X550 LAN1/LAN2** enabled.
7. **After a power cut** the board may only arm Wake-on-LAN after one normal boot and shutdown. Power it on once by hand or with IPMI.
8. **Firmware:** ASRock Rack's [X570D4U-2L2T page](https://www.asrockrack.com/general/productdetail.asp?Model=X570D4U-2L2T#Download) has the latest BIOS and BMC. Update both if a newer one is out.

## IPMI or Wake-on-LAN?

The X570D4U-2L2T has a BMC, so `ipmi` is available too, and it is the more reliable of the two: the BMC runs on standby power with its own port (IPMI_LAN1), works after a power cut, doesn't depend on the switch's link speed, reports whether the box is really on or off, and can do a soft power-off. Wake-on-LAN needs no BMC user or password, and is a good fallback.

The container uses whichever methods are configured, in the order you set, and tries the next one if a method fails:

| `POWER_ON_ORDER` | When to use it |
|------------------|----------------|
| `ipmi,wol` (default) | IPMI set up, Wake-on-LAN as a fallback. Recommended for this board. |
| `wol,ipmi` | Prefer Wake-on-LAN, fall back to IPMI. |
| `wol` | No IPMI user set up. Pair it with `POWER_OFF_ORDER=api` (shutdown through the backup box's own API). |

IPMI setup is in [Setup](setup.md#ipmi-optional).
