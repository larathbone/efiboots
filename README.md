# EFI Boots

Manage EFI boot loader entries with this simple GUI.

This is how it looks on my machine:  
![This is EFI Boots in action](screenshot.png)

You can:

- reorder, add, delete, enable or disable boot entries
- choose what to boot into at the next reboot (NextBoot)
- set the time to wait before the first entry (or the NextBoot one) is selected

Beware that efibootmgr acts on EFI variables and that could be dangerous on
non-standard compliant implementations.

## Dependencies

This project requires these libraries to be installed on your system:
- python 3
- efibootmgr
- gtk 4
- python gobject

### Debian GNU/Linux, Ubuntu and other derivatives

```
sudo apt install efibootmgr python3 python3-gi libgtk-4-1
```

### Arch, Manajaro and other derivatives

```
sudo pacman -S --needed efibootmgr python3 python-gobject gtk4
```

### nix

```
nix-build
nix-env -i $(realpath result)
```

## Usage

```
$ python3 efiboots
```

If the program is unable to correctly auto-detect the ESP (EFI System Partition)
you can manually pass --disk and --part like this (assuming ESP is on /dev/sda1):

```
$ python3 efiboots --disk /dev/sda --part 1
```

You can also [report the issue](https://github.com/Elinvention/efibootmgr-gui/issues/new),
so that I can improve the auto-detection algorithm.

## Contributing

Contributions are welcome. Development happens on the ["main" branch](https://github.com/Elinvention/efibootmgr-gui/tree/main).
