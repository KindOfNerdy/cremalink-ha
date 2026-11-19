# <img src="icon.png" alt="Cremalink Logo" height="45" style="vertical-align: middle; margin-right: 10px;"> cremalink for Home Assistant

**The official Home Assistant integration for monitoring and controlling IoT coffee machines via Cremalink.**

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?category=integration&repository=cremalink-ha&owner=lodzen)
[![License](https://img.shields.io/github/license/lodzen/cremalink-ha?style=for-the-badge&color=success)](LICENSE)
[![Source Code](https://img.shields.io/badge/Source-GitHub-black?style=for-the-badge&logo=github)](https://github.com/lodzen/cremalink-ha)

---

## ✨ Overview

This integration connects your Home Assistant instance to the **Cremalink** ecosystem, allowing for real-time state monitoring and control of smart coffee machines. Local (LAN) connectivity is handled by a built-in, embedded server hosted directly by the integration — **no separate add-on is required**.

> [!NOTE] 
> This project was developed with a result-oriented approach, primarily optimized for the **De'Longhi PrimaDonna Soul**. While the architecture is designed to be extensible, some logic may currently be tightly coupled to this specific model.
>
> The goal is to make the library fully generic. If you encounter issues with other machines, contributions are highly encouraged!

> [!NOTE]
> **cremalink-ha** acts solely as a bridge to Home Assistant. Device management (e.g., adding new machines) is handled exclusively via the main **[cremalink](https://github.com/lodzen/cremalink)** project. Please set up your devices there before using this integration.
---

## 🚀 Installation

### 1. Install the Integration

**Via HACS (Recommended):**
1.  Click the "Open your Home Assistant instance" badge above, or manually add this repository to HACS as a custom repository.
2.  Search for "Cremalink" and install.
3.  Restart Home Assistant.

**Manual Installation:**
1.  Copy the `custom_components/cremalink_ha` folder to your `config/custom_components/` directory.
2.  Restart Home Assistant.

> [!NOTE]
> The **Cremalink Server Add-on** is deprecated for this integration — local mode
> now runs entirely in-process, with no Supervisor add-on to install, configure, or
> keep running. If you have an existing add-on-based setup, see
> [Upgrading from the add-on](#-upgrading-from-the-add-on) below.

---

## ⚙️ Configuration

1.  Navigate to **Settings** > **Devices & Services**.
2.  Click **Add Integration**.
3.  Search for **Cremalink**.
4.  Sign in with your Cremalink cloud account email and password. The integration
    automatically discovers your coffee machine(s), detects the correct model, and
    connects locally (via the built-in embedded server) whenever LAN details are
    available, falling back to the cloud connection otherwise — no device map,
    DSN, LAN key, or add-on to set up.
5.  Don't have (or don't want to use) a cloud account? Check **Advanced setup** on
    the first screen to fall back to the previous manual flow: picking a device map
    and entering the DSN/LAN key/IP (local) or a refresh token (cloud) by hand.

### Entities

Beyond the monitor sensors, binary sensors, and the power switch, the
integration exposes:

- **Statistics sensors** (local connections only): native `0xA2` usage
  counters — total beverages, per-drink counts, descales, milk cleans,
  filter changes, and total water (litres). Fetched on a slow ~5-minute
  lane; cloud entries on `native` maps get none (the Ayla cache is
  stale there). `cloud_counters` maps (e.g. Eletta-family machines
  without LAN support) expose their counters on cloud entries instead,
  plus the grounds container, descale and filter percentages exactly as
  the machine publishes them, and aborted, iced and cold brew totals
  where the map lists those datapoints.
- **Freshness sensors** (diagnostic timestamps): "Last report" is when the
  machine last reported its status; on `cloud_counters` maps "Statistics
  updated" shows when the cloud last received the counters. Counters
  reach the cloud only now and then, so this tells how old they are.
  Both keep their last value while the machine sleeps or is unreachable.
- **Profile select**: lists the occupied machine profiles by name;
  changing it sends a session-gated `0xA9` write — the entity only
  shows machine-confirmed selections. The machine's own selected
  profile is also re-read on monitor cycles (param `0x03EE` — reads
  answer even in standby), so changes made on the machine's panel show
  up in the entity.
- **Settings selects**: auto-off time, water hardness and coffee temperature, writable via
  session-gated `0x90` commands with read-back confirmation.
- **Bean System select** (BeanAdapt machines only): lists the configured
  bean systems by the names you assigned on the machine (read from the
  `d25x` bean blobs). Changing it sends the session-gated `0xB9` select
  frame — protocol-derived and not yet live-verified, so it is gated on
  machine readiness like the profile select. Bean names themselves are
  read-only: the `0xBB` name write is not implemented.
- **Firmware** (diagnostic): the Wi-Fi module's firmware as a sensor
  (with the date the cloud last updated it); the machine's own firmware
  shows on the device page. Read once at startup from the cloud — a
  changed value after a restart means the cloud installed an update; the
  cloud does not announce pending updates.
- **Settings switches** (device *Configuration* section): beep tone and
  energy saving — boolean flags merged into the shared user-config
  parameter via session-gated `0x90` writes with read-back confirmation.
- **Recipe select + Brew button**: the single brew surface. Pick any
  machine-catalogued drink in the *Recipe* select — including Bean
  Adapt drinks (e.g. *Espresso Soul*), which brew with the bean
  currently selected on the machine — then press *Brew*. The previous
  per-drink buttons are gone: brewing now flows through the select.
  - **Parameter sliders** (number entities): for the selected recipe,
    the user-adjustable parameters (coffee amount, milk, taste,
    temperature…) appear with the machine's own min/default/max bounds,
    preloaded with the values stored under the active profile
    (falling back to machine defaults while the profile read is still
    pending — see the `values_source` attribute). Moving a slider only
    stages the value — nothing is written to the machine until you
    press *Brew*, and the adjustment applies to that brew only: it is
    never persisted on the machine.
  - **Safety gate:** after you change the recipe or the active profile,
    the sliders and *Brew* stay unavailable until the values for the
    new combination are loaded — the machine never brews (or edits)
    stale values from the previous selection.
  - **Recipe catalogue, fast and quiet**: the recipe list itself is
    static (no machine traffic). Like the official app, the per-recipe
    bounds and the drink-menu order can be loaded with **one** cloud
    request: open the integration's *Configure* dialog and enter your
    cloud e-mail and password once (the password is never stored — only
    the refresh token is kept; entries created through the cloud login
    keep it automatically). Without it — or if the cloud is
    unreachable — the integration falls back to reading the machine over
    the LAN: one request per recipe on first use, then cached. Cloud
    connections load the same snapshot through their own session. The
    current values of a recipe are always read live from the machine.
  - Custom recipes appear under the names given on the machine.
  - The brew switch turns on right away and stays on until the machine
    reports the drink (over the cloud that can take ~30 s); a second tap
    in that time does not start a second drink.
- **Stop brewing**: unchanged — available while a brew runs.
- **Brew action** (`cremalink_ha.brew`): one call for automations and
  scripts. Give the machine (`device_id`) and the drink (name, command
  such as `cappuccino`, custom recipe name, or beverage id), optionally
  with `coffee` (ml), `taste`, `milk` (s), `water` (ml) or
  `tea_temperature`. Amounts left out come from the recipe stored in the
  active profile; given ones are clamped to the machine's limits for
  that drink. Nothing is saved on the machine.

  ```yaml
  action: cremalink_ha.brew
  data:
    device_id: <your machine>
    beverage: cappuccino
    coffee: 60
    milk: 25
  ```

- **Favourites action** (`cremalink_ha.set_favorites`): writes the favourite
  drinks of a profile (`profile`, default: the active one). Give the
  machine (`device_id`) and `favorites`, the complete list in favourite
  order, first one first (at most 12; drink name, command, custom recipe
  name or beverage id). The machine replaces its list and the action
  reports an error if it does not confirm. The current list is the
  `favorite_beverages` attribute of the recipe select.

  ```yaml
  action: cremalink_ha.set_favorites
  data:
    device_id: <your machine>
    favorites: [coffee, latte_macchiato, flat_white]
  ```

- **Custom recipe actions** (`cremalink_ha.save_recipe`, `cremalink_ha.delete_recipe`):
  create, change or empty one of the machine's six custom recipes (`slot` 1-6).
  `save_recipe` takes a `name` (up to 10 characters), an `icon` (the picture number
  of the machine's display, 0-25) and optionally `template` (a drink whose stored
  recipe is the starting point; default: the recipe already in the slot), the amounts
  `coffee`, `taste`, `milk`, `water`, `tea_temperature`, and `brew: true` to also pour
  it. The machine must be awake; both actions report an error if the machine does not
  confirm.

  ```yaml
  action: cremalink_ha.save_recipe
  data:
    device_id: <your machine>
    slot: 6
    name: Hot water
    icon: 12
    template: hot_water
    water: 50
  ```

- **Recipe list refresh**: the list of recipes (names, pictures, favorites) is
  read from the cloud again every 30 minutes (one request, the machine is not
  asked), so recipes created or renamed on the machine or in the app show up
  without a restart. Change the interval (0 to 240 minutes, 0 = off) in the
  integration's *Configure* dialog, or reload right now with the
  `cremalink_ha.refresh_recipes` action (`device_id`).

> **Migrating from per-drink buttons:** automations or dashboards that
> called `button.press` on `button.brew_espresso` (etc.) need updating:
> call `select.select_option` on `select.<machine>_recipe` with the
> drink name, then `button.press` on `button.<machine>_brew`. Parameter
> overrides can be staged first via `number.set_value` on the
> `number.<machine>_…` slider entities — or use the single
> `cremalink_ha.brew` action instead.

> **A note on writes:** machine writes are announced through a short
> (~300 s) session, which the integration manages for you. A write can
> take a second or two to confirm and only shows up in the entity once
> the machine acknowledged it — a failed write leaves the previous
> option in place.

> **State across restarts:** entities backed by machine-confirmed state
> (statistics counters, profile/settings selects, bean-system select,
> recipe selection) keep their last value after a Home Assistant
> restart — standby blocks the machine's datapoint reads, so restored
> values are shown until a fresh read lands; restored state never sends
> anything to the machine. Live monitor entities (status, brew,
> progress) always reflect the real machine state and are not
> restored.

---

## ⚙️ Upgrading from the add-on

If you previously set up local mode using the **Cremalink Server Add-on**, that
entry is flagged as needing reconfiguration after upgrading (visible under
**Settings > Repairs**, and via a "Reconfigure" option on the device's config
entry). Your DSN, device name, and device map are reused automatically —
completing the reconfigure step switches the entry to the built-in embedded
server, after which you can uninstall the add-on.

---

## 🛡️ What happens to the embedded server during a reload, restart, or crash?

Since the embedded server runs *inside* Home Assistant's own process, it's
worth being explicit about what's guaranteed in each scenario:

- **Config entry reload / unload / removal** — the embedded server is
  stopped and its port released before the entry finishes unloading. A
  stuck device connection can no longer make this hang: the server's own
  teardown has a short internal bound, and the integration additionally
  wraps every stop with its own bounded timeout, so a reload/removal
  always completes within a few seconds even in the worst case.
- **A full Home Assistant restart** — Home Assistant does not unload
  config entries individually as part of a normal restart; the whole
  process exits and is relaunched by whatever manages it (systemd,
  Docker, the Supervisor, etc.). The operating system reclaims every
  socket and background task on process exit, and the embedded server's
  automatic port-fallback already tolerates binding again immediately
  afterward — no cleanup needs to run for a restart to be safe.
- **A hard crash (power loss, OOM, `SIGKILL`)** — identical outcome to a
  restart: nothing runs, the OS reclaims everything, and the next start
  begins clean.

In short: a problem in the embedded server cannot destabilize Home
Assistant itself in any of these scenarios.

---

## 🤝 Contributing

Contributions are welcome! If you have a machine profile not yet supported, please check the [Project Wiki of the official cremalink repository](https://github.com/lodzen/cremalink/wiki/) on how to add new definitions.

---

## 💫 Star History

[![Star History Chart](https://api.star-history.com/svg?repos=lodzen/cremalink-ha&type=date&logscale&legend=top-left)](https://www.star-history.com/#lodzen/cremalink-ha&type=date&logscale&legend=top-left)

## 📄 License

Distributed under the **AGPL-3.0-or-later** License. See `LICENSE` for more information.

---

*Developed by [Midian Tekle Elfu](mailto:developer@midian.tekleelfu.de). Supported by the community.*
