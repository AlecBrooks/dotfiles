# Addie's Arch Linux Dotfiles

A GNU Stow-managed collection of configuration files for my Arch Linux desktop.

The setup is centered around **Hyprland configured in Lua**, Kitty, Bash with Starship, Yazi, Lazygit, Sublime Text, Helix, and a collection of custom terminal utilities.

## Included configuration

| Package | Installed location |
|---|---|
| `shell` | `~/.bashrc`, `~/.bash_profile` |
| `starship` | `~/.config/starship.toml` |
| `hypr` | `~/.config/hypr/` |
| `kitty` | `~/.config/kitty/` |
| `yazi` | `~/.config/yazi/` |
| `lazygit` | `~/.config/lazygit/` |
| `helix` | `~/.config/helix/` |
| `sublime` | `~/.config/sublime-text/Packages/User/` |
| `gtk` | `~/.config/gtk-3.0/`, `~/.config/gtk-4.0/` |
| `ytjukebox` | `~/.config/ytjukebox/` |
| `scripts` | `~/bin/` |

The `packages/` directory also contains lists of explicitly installed Pacman and AUR packages.

## Highlights

- Arch Linux and Hyprland
- Hyprland configuration written in Lua
- Dual-monitor workspace-pair scripts
- Kitty with a Gruvbox Teal color scheme
- Bash prompt powered by Starship
- Yazi and Lazygit terminal workflows
- Sublime Text with a persistent tmux build workflow
- Helix terminal editor configuration
- GTK theme integration
- Custom OBS/Veadotube control panel
- `ytjukebox`, a terminal YouTube music player with an embedded looping video visualizer
- Utility scripts for project creation, CSV inspection, workspace movement, and native builds

## Requirements

The installation script is intended for Arch Linux or an Arch-based system.

At minimum, it requires:

- Bash
- Git
- GNU Stow

The complete package lists are stored in:

```text
packages/pacman.txt
packages/aur.txt
```

The GTK configuration expects the following theme to exist:

```text
/usr/share/themes/Gruvbox-Teal-Dark
```

## Installation

Clone the repository into your home directory:

```bash
git clone https://github.com/YOUR-USERNAME/dotfiles.git ~/dotfiles
cd ~/dotfiles
```

Run the installer:

```bash
./install.sh
```

The installer will:

1. Install packages from `packages/pacman.txt`.
2. Install AUR packages with `yay` or `paru` when one is available.
3. Back up conflicting existing configuration files.
4. Use GNU Stow to link each package into your home directory.

To install only the configuration links without installing packages:

```bash
./install.sh --stow-only
```

To install Pacman packages but skip AUR packages:

```bash
./install.sh --no-aur
```

## How GNU Stow works

This repository mirrors the layout of the home directory. For example:

```text
~/dotfiles/kitty/.config/kitty/kitty.conf
```

is linked to:

```text
~/.config/kitty/kitty.conf
```

The repository is the source of truth. Editing either path normally edits the same underlying file because the home-directory path is a symbolic link into this repository.

For clarity, I generally edit files directly inside `~/dotfiles`, then commit the changes:

```bash
cd ~/dotfiles
git status
git add .
git commit -m "Describe the configuration change"
git push
```

After cloning onto another machine, running `./install.sh` recreates the links.

## Manually restowing

To refresh all links after adding or reorganizing a package:

```bash
cd ~/dotfiles

stow --restow --target="$HOME" \
  shell \
  starship \
  hypr \
  kitty \
  yazi \
  lazygit \
  helix \
  sublime \
  gtk \
  ytjukebox \
  scripts
```

To remove the links without deleting the repository files:

```bash
cd ~/dotfiles

stow --delete --target="$HOME" \
  shell \
  starship \
  hypr \
  kitty \
  yazi \
  lazygit \
  helix \
  sublime \
  gtk \
  ytjukebox \
  scripts
```

## Machine-specific files

Secrets, generated files, and machine-specific state are intentionally excluded from Git.

Examples include:

- `~/bin/obs-password`
- `.env` files
- API keys and tokens
- Sublime Package Control generated certificate bundles
- `playlist.toml.save`
- Python cache files
- logs and temporary files

The OBS password file can be created locally after installation:

```bash
printf '%s\n' 'YOUR_OBS_WEBSOCKET_PASSWORD' > ~/bin/obs-password
chmod 600 ~/bin/obs-password
```

Do not commit that file.

The `ytjukebox` playlist may also contain a local visualizer path that needs to be adjusted for another machine:

```toml
[visualizer]
enabled = true
file = "/home/YOUR-USER/Videos/ytjukebox/visualizer.mp4"
```

## Updating package lists

Refresh the native package list:

```bash
pacman -Qqen | sort > ~/dotfiles/packages/pacman.txt
```

Refresh the foreign/AUR package list:

```bash
pacman -Qqem | sort > ~/dotfiles/packages/aur.txt
```

Commit the updated lists afterward.

## Safety

Before publishing changes, scan the repository for likely secrets:

```bash
cd ~/dotfiles

rg -n -i \
  'password|passwd|token|secret|api[_-]?key|client[_-]?secret|authorization|bearer' \
  . \
  --glob '!.git/**'
```

A reference to a password-loading function is not itself a secret. Verify that no actual credential value is present.

Also check staged changes before committing:

```bash
git diff --cached --check
git status --short
```

## License

These files are provided as a personal configuration reference. Reuse and adapt them at your own risk.
