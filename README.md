# asus-dts-linux

asus laptops ship a DTS speaker tuning per model inside their driver. this
pulls yours out and runs it in pipewire, so the built-in speakers sound
like they're supposed to on linux.

headphones and bluetooth are left alone.

how i figured it out: [docs/how-it-works.md](docs/how-it-works.md)

## install

fedora:

```
sudo dnf install pipewire-module-filter-chain-lv2 lsp-plugins-lv2 calf p7zip p7zip-plugins
./asus-dts-linux.py install
```

it detects your model, downloads the driver from asus and sets up
pipewire. if detection fails, use `--ssid`, `--sink` or `--driver`.

```
./asus-dts-linux.py uninstall
```

## tested

| laptop | id |
|---|---|
| vivobook 15x oled k3502za | 1043175F |

the driver has tunings for 256 asus models. tried it on yours? open an
issue with your model and id (`grep Subsystem /proc/asound/card*/codec#*`).

## license

MIT. no asus or dts files are in this repo. the tuning is downloaded from
asus on your machine and belongs to asus / xperi.
