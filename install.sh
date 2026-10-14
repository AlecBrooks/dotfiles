#!/usr/bin/env bash

set -Eeuo pipefail

REPO_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_PACKAGES=true
INSTALL_AUR=true
INSTALL_PERSONAL=false
SETUP_SERVICES=true

STOW_PACKAGES=(
  shell
  starship
  hypr
  kitty
  yazi
  lazygit
  helix
  sublime
  gtk
  ytjukebox
  scripts
  nvim
  fastfetch
  waybar
  matugen
  wallpaper-picker
)

# Tied to the original owner's accounts (Radicale calendar/contacts); only with --personal.
PERSONAL_PACKAGES=(
  pim
)

MANAGED_PATHS=(
  ".bashrc"
  ".bash_profile"
  ".config/starship.toml"
  ".config/hypr"
  ".config/kitty"
  ".config/yazi"
  ".config/lazygit"
  ".config/helix"
  ".config/nvim"
  ".config/sublime-text/Packages/User"
  ".config/gtk-3.0"
  ".config/gtk-4.0"
  ".config/ytjukebox"
  ".config/fastfetch"
  ".config/waybar"
  ".config/matugen"
  ".config/systemd/user/wallpaper-picker-start.service"
  ".local/bin/wallpaper-picker"
  ".local/share/wallpaper-picker"
  ".local/share/applications/wallpaper-picker.desktop"
  "bin"
)

PERSONAL_PATHS=(
  ".config/calcurse"
  ".config/vdirsyncer"
  ".config/khard"
  ".config/systemd/user/calcurse-sync.service"
  ".config/systemd/user/calcurse-sync.timer"
)

# Real directories that must exist before stowing. Otherwise Stow "folds" a missing parent into a
# single symlink (e.g. ~/.config/systemd -> the repo) and later writes land inside the repo.
REAL_DIRS=(
  ".config"
  ".config/systemd/user"
  ".config/sublime-text/Packages"
  ".local/bin"
  ".local/share/applications"
  ".local/state"
  ".cache"
)

usage() {
  cat <<'USAGE'
Usage: ./install.sh [OPTIONS]

Install packages and link the dotfiles into the current user's home directory.

Options:
  --stow-only    Skip Pacman and AUR package installation
  --no-packages  Alias for --stow-only
  --no-aur       Install Pacman packages but skip AUR packages
  --personal     Also link the owner's personal configs (pim: Radicale calendar/contacts)
  --no-services  Don't enable systemd user units or generate the first theme
  -h, --help     Show this help message
USAGE
}

info() {
  printf '\033[1;34m==>\033[0m %s\n' "$*"
}

warn() {
  printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2
}

die() {
  printf '\033[1;31merror:\033[0m %s\n' "$*" >&2
  exit 1
}

while (($# > 0)); do
  case "$1" in
    --stow-only|--no-packages)
      INSTALL_PACKAGES=false
      INSTALL_AUR=false
      ;;
    --no-aur)
      INSTALL_AUR=false
      ;;
    --personal)
      INSTALL_PERSONAL=true
      ;;
    --no-services)
      SETUP_SERVICES=false
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "Unknown option: $1"
      ;;
  esac
  shift
done

if [[ ${EUID} -eq 0 ]]; then
  die "Run this script as your normal user, not as root."
fi

if [[ ! -d "$REPO_DIR/.git" ]]; then
  warn "No .git directory was found. Continuing because the script may be running from an archive."
fi

read_package_list() {
  local file="$1"
  local -n output_array="$2"

  output_array=()
  [[ -f "$file" ]] || return 0

  while IFS= read -r package; do
    package="${package%%#*}"
    package="${package#"${package%%[![:space:]]*}"}"
    package="${package%"${package##*[![:space:]]}"}"
    [[ -n "$package" ]] && output_array+=("$package")
  done < "$file"
}

install_native_packages() {
  [[ -f /etc/arch-release ]] || die "Package installation is supported only on Arch Linux."

  info "Refreshing package databases and installing Git and GNU Stow"
  sudo pacman -S --needed git stow

  local packages=()
  read_package_list "$REPO_DIR/packages/pacman.txt" packages

  if ((${#packages[@]} == 0)); then
    warn "No native packages were found in packages/pacman.txt"
    return
  fi

  info "Installing ${#packages[@]} native packages"
  sudo pacman -S --needed "${packages[@]}"
}

install_aur_packages() {
  local packages=()
  read_package_list "$REPO_DIR/packages/aur.txt" packages

  if ((${#packages[@]} == 0)); then
    info "No AUR packages are listed"
    return
  fi

  local helper=""
  if command -v yay >/dev/null 2>&1; then
    helper="yay"
  elif command -v paru >/dev/null 2>&1; then
    helper="paru"
  fi

  if [[ -z "$helper" ]]; then
    warn "AUR packages are listed, but neither yay nor paru is installed."
    warn "Install an AUR helper, then install the packages in packages/aur.txt."
    return
  fi

  info "Installing ${#packages[@]} AUR packages with $helper"
  "$helper" -S --needed "${packages[@]}"
}

path_is_managed_by_repo() {
  local target="$1"
  local resolved=""

  [[ -L "$target" ]] || return 1
  resolved="$(realpath -m -- "$target" 2>/dev/null || true)"
  [[ "$resolved" == "$REPO_DIR" || "$resolved" == "$REPO_DIR/"* ]]
}

# Make each REAL_DIRS entry a real directory. If an earlier run let Stow fold one of its parents
# into a single link to the repo (e.g. all of ~/.config/sublime-text), remove that link first;
# Stow then recreates finer-grained links inside the real directory.
make_real_dirs() {
  for dir in "${REAL_DIRS[@]}"; do
    local prefix="" ok=true part
    local -a parts
    IFS='/' read -r -a parts <<<"$dir"
    for part in "${parts[@]}"; do
      prefix="${prefix:+$prefix/}$part"
      if [[ -L "$HOME/$prefix" ]]; then
        if path_is_managed_by_repo "$HOME/$prefix"; then
          info "Unfolding $HOME/$prefix"
          rm -- "$HOME/$prefix"
        else
          warn "$HOME/$prefix is a symlink outside this repo; not creating $HOME/$dir"
          ok=false
          break
        fi
      fi
    done
    [[ "$ok" == true ]] && mkdir -p -- "$HOME/$dir"
  done
}

backup_conflicts() {
  local timestamp
  timestamp="$(date +%Y%m%d-%H%M%S)"

  local backup_dir="$HOME/dotfiles-preinstall-backup-$timestamp"
  local moved_any=false

  local paths=("${MANAGED_PATHS[@]}")
  [[ "$INSTALL_PERSONAL" == true ]] && paths+=("${PERSONAL_PATHS[@]}")

  for relative_path in "${paths[@]}"; do
    local target="$HOME/$relative_path"

    if [[ ! -e "$target" && ! -L "$target" ]]; then
      continue
    fi

    # Never "back up" through a link into the repo: that would move the repo's own files.
    local parent_real
    parent_real="$(realpath -m -- "$(dirname -- "$target")")"
    if [[ "$parent_real" == "$REPO_DIR" || "$parent_real" == "$REPO_DIR/"* ]]; then
      continue
    fi

    if path_is_managed_by_repo "$target"; then
      # A hand-made absolute link into the repo: Stow refuses to replace links it didn't make,
      # so drop it and let Stow recreate the same link.
      if [[ "$(readlink -- "$target")" == /* ]]; then
        info "Replacing hand-made link $target"
        rm -- "$target"
      fi
      continue
    fi

    local destination="$backup_dir/$relative_path"
    mkdir -p -- "$(dirname -- "$destination")"

    info "Backing up $target"
    mv -- "$target" "$destination"
    moved_any=true
  done

  if [[ "$moved_any" == true ]]; then
    info "Existing files were backed up to:"
    printf '    %s\n' "$backup_dir"
  else
    rmdir --ignore-fail-on-non-empty "$backup_dir" 2>/dev/null || true
  fi
}

stow_dotfiles() {
  command -v stow >/dev/null 2>&1 || die "GNU Stow is not installed."

  local wanted=("${STOW_PACKAGES[@]}")
  [[ "$INSTALL_PERSONAL" == true ]] && wanted+=("${PERSONAL_PACKAGES[@]}")

  local available_packages=()
  for package in "${wanted[@]}"; do
    if [[ -d "$REPO_DIR/$package" ]]; then
      available_packages+=("$package")
    else
      warn "Skipping missing Stow package: $package"
    fi
  done

  ((${#available_packages[@]} > 0)) || die "No Stow packages were found."

  info "Linking dotfiles into $HOME"
  stow \
    --dir="$REPO_DIR" \
    --target="$HOME" \
    --restow \
    --verbose=1 \
    "${available_packages[@]}"
}

setup_user() {
  mkdir -p "$HOME/Photos/Screenshots"

  [[ "$SETUP_SERVICES" == true ]] || return 0

  # The theme (colors for Waybar, kitty, hyprlock, GTK, Qt...) is generated per user into
  # ~/.cache/wallpaper-theme. Make a first one from Hyprland's bundled wallpaper so nothing
  # starts without its colors; picking a Wallpaper Engine wallpaper later regenerates it.
  if [[ ! -f "$HOME/.cache/wallpaper-theme/waybar-colors.css" ]]; then
    if command -v matugen >/dev/null 2>&1 && [[ -f /usr/share/hypr/wall0.png ]]; then
      info "Generating the first color theme"
      "$HOME/bin/wallpaper-theme" --image /usr/share/hypr/wall0.png --no-reload --no-restart-apps \
        || warn "wallpaper-theme failed; run it again after logging in"
    else
      warn "matugen or /usr/share/hypr/wall0.png is missing; run ~/bin/wallpaper-theme --image <file> later"
    fi
  fi

  if systemctl --user show-environment >/dev/null 2>&1; then
    info "Enabling systemd user units"
    systemctl --user daemon-reload
    systemctl --user enable wallpaper-picker-start.service
    if [[ "$INSTALL_PERSONAL" == true ]]; then
      systemctl --user enable calcurse-sync.timer
    fi
  else
    warn "No systemd user session; enable wallpaper-picker-start.service after logging in:"
    warn "  systemctl --user enable wallpaper-picker-start.service"
  fi
}

main() {
  info "Dotfiles repository: $REPO_DIR"

  if [[ "$INSTALL_PACKAGES" == true ]]; then
    install_native_packages

    if [[ "$INSTALL_AUR" == true ]]; then
      install_aur_packages
    fi
  fi

  make_real_dirs
  backup_conflicts
  stow_dotfiles
  setup_user

  if [[ ! -d /usr/share/themes/Gruvbox-Teal-Dark ]]; then
    warn "Gruvbox-Teal-Dark was not found under /usr/share/themes (optional: only the"
    warn "~/.config/gtk-4.0/assets icons link to it; colors come from the generated theme)."
  fi

  cat <<'DONE'

Installation complete.

Suggested follow-up:

  1. Start a new shell or run:
       exec bash -l

  2. Log out and back in to Hyprland. Waybar and the wallpaper start from
     wallpaper-picker-start.service. To use animated wallpapers, sign in to
     Steam, subscribe to Wallpaper Engine items, then open "Wallpaper Picker".

  3. Optional, per-user and not tracked:
       ~/bin/obs-password      OBS websocket password (chmod 600)
       ~/.config/ytjukebox/playlist.toml   visualizer video path
DONE
}

main "$@"
