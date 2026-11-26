# Wake-on-LAN on an ASRock Rack X570D4U-2L2T

How to set up an ASRock Rack **X570D4U-2L2T** backup box running TrueNAS SCALE **25.10** or later so the container can wake it with Wake-on-LAN (`wol`). The general steps are in [Setup](setup.md#wake-on-lan-optional); this page fills in the details for this board.

This board also has a BMC, so it can be powered on with **IPMI** as well. See [IPMI or Wake-on-LAN?](#ipmi-or-wake-on-lan) at the end.

## Which port to use

The board has four network ports for the host, plus a dedicated IPMI port:

| Ports | Controller | Linux driver | Wake-on-LAN |
|-------|------------|--------------|-------------|
| 2 x 1GbE | Intel i210 | `igb` | Yes. **Use one of these.** |
| 2 x 10GbE | Intel X550-AT2 | `ixgbe` | Depends on the board's NIC firmware; often not offered. Check with `ethtool` (below) before relying on it. |
| 1 x IPMI | BMC | (none) | Not a host port: this is the BMC's own network port, used by `ipmi`. |

Plug one **i210** port into the same LAN (and VLAN) as the primary, even if the backup box moves its data over a 10GbE port. The magic packet is a broadcast, so it only reaches ports on the primary's network segment. While the box is off, the i210 port links at a low speed: the switch port must stay up for that (no port shutdown or strict power saving on it).

## 1. BIOS

Reboot into the BIOS (press **F2** or **Del** at the ASRock Rack logo, or use the BMC's remote console). Names and places vary a little between BIOS versions; these are the settings that matter:

| Setting | Usually under | Set to | Why |
|---------|---------------|--------|-----|
| **PCIE Devices Power On** | Advanced > ACPI Configuration | Enabled | The onboard i210 and X550 sit on PCIe, so this is the switch that lets them wake the board. |
| **Deep S5** / **ErP Ready** (if present) | Advanced > ACPI Configuration, or Chipset | Disabled | With it on, the board cuts standby power to the network ports and nothing can wake it. |
| **Restore on AC/Power Loss** | Advanced > Chipset Configuration, or Server Mgmt | Power Off (or Last State) | The box should stay off after a power cut and wait for the next run. |

Save and exit (**F10**).

## 2. Find the port's interface name and MAC

On the backup box: **System > Shell**.

```
ip -br link
```

lists the interfaces, e.g. `enp5s0`, `enp6s0` (the names depend on the slot layout, so check rather than copy). For each one, the driver tells you the controller:

```
ethtool -i enp5s0 | grep driver     # igb = i210 (1GbE), ixgbe = X550 (10GbE)
```

Pick the `igb` port that is cabled to the LAN (`ip -br link` shows it `UP`). Its MAC is the `link/ether` address in:

```
ip link show enp5s0
```

It is also shown in the web UI under **Network > Interfaces** (click the interface). This MAC goes into `WOL_MAC` on the primary.

Then check that the port supports Wake-on-LAN:

```
ethtool enp5s0 | grep -i wake
```

```
	Supports Wake-on: pumbg
	Wake-on: d
```

`Supports Wake-on` must contain **g** (magic packet). `Wake-on: d` means it is disabled right now, which the next step fixes. If you try a 10GbE port and `Supports Wake-on` is only `d`, that port can't wake the box: use an i210 port.

## 3. Turn Wake-on-LAN on in TrueNAS, persistently

Linux and TrueNAS can leave Wake-on-LAN off on the card, and a setting made in the shell is lost at the next boot. So set it on every boot with an init script:

1. **System > Advanced Settings > Init/Shutdown Scripts > Add**.
2. **Description**: `Wake-on-LAN`. **Type**: Command. **Command**: `ethtool -s enp5s0 wol g` (your interface). **When**: Post Init. **Enabled**: on.
3. Save, then run the same command once in the shell (or reboot) so it applies now.

Check it:

```
ethtool enp5s0 | grep -i wake
```

Now `Wake-on` should be `g`. If the interface name ever changes (for example after adding a PCIe card), update the script.

## 4. Configure the container on the primary

In the container's `.env` on the **primary**:

```
WOL_MAC=aa:bb:cc:dd:ee:ff        # the i210 port's MAC from step 2
WOL_BROADCAST=192.168.1.255      # your LAN's broadcast address
POWER_ON_ORDER=ipmi,wol          # or wol,ipmi, or just wol (see below)
```

Recreate the container so it picks up the change. The container must use `network_mode: host` (as in the compose example), or the broadcast never leaves Docker's network.

## 5. Test it

1. Shut the backup box down normally from its web UI (power button icon > **Shut Down**) and wait until it is off. Wake-on-LAN wakes the box from a clean shutdown.
2. On the primary (**System > Shell**), send one magic packet from the container, with your MAC (without colons) and broadcast:

   ```
   docker exec truenas-backup python -c "import socket; m = bytes.fromhex('aabbccddeeff'); s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1); s.sendto(b'\xff' * 6 + m * 16, ('192.168.1.255', 9))"
   ```

3. The box should power on within a few seconds. Once it has booted, check that `ethtool <interface> | grep Wake-on` still shows `g`, so the init script ran.
4. Test the whole power-on path the way the schedule uses it, with only Wake-on-LAN: shut the box down again, set `POWER_ON_ORDER=wol` temporarily, recreate the container and run `docker exec truenas-backup backup --no-shutdown`. Put your real order back afterwards.

If it doesn't wake:

- `Wake-on` was `d` at shutdown: the init script didn't run or names the wrong interface.
- The link lights on the i210 port are dark while the box is off: standby power is cut. Check **Deep S5** / **ErP** in the BIOS, and that the power supply is on.
- Wrong MAC (the MAC of another port) or a broadcast address from another subnet or VLAN.
- After a power cut, some boards only arm Wake-on-LAN after one normal boot and shutdown. Power it on once by hand (or with IPMI). This is one reason to keep IPMI configured as well.

## IPMI or Wake-on-LAN?

The X570D4U-2L2T has a BMC, so `ipmi` is available too, and it is the more reliable of the two: the BMC runs on standby power with its own network port, works after a power cut, reports whether the box is really on or off, and can do a soft power-off. Wake-on-LAN needs no BMC user or password, and is a good fallback if the BMC is unreachable.

The container uses whichever methods are configured, in the order you set, and tries the next one if a method fails:

| `POWER_ON_ORDER` | When to use it |
|------------------|----------------|
| `ipmi,wol` (default) | IPMI set up, Wake-on-LAN as a fallback. Recommended for this board. |
| `wol,ipmi` | Prefer Wake-on-LAN, fall back to IPMI. |
| `wol` | No IPMI user set up. Pair it with `POWER_OFF_ORDER=api` (shutdown through the backup box's own API). |

IPMI setup is in [Setup](setup.md#ipmi-optional).
