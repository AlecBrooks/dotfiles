#!/usr/bin/env bash

set -Eeuo pipefail

REPO_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_PACKAGES=true
INSTALL_AUR=true

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
  ".config/sublime-text/Packages/User"
  ".config/gtk-3.0"
  ".config/gtk-4.0"
  ".config/ytjukebox"
  "bin"
)

usage() {
  cat <<'USAGE'
Usage: ./install.sh [OPTIONS]

Install packages and link the dotfiles into the current user's home directory.

Options:
  --stow-only    Skip Pacman and AUR package installation
  --no-packages  Alias for --stow-only
  --no-aur       Install Pacman packages but skip AUR packages
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

backup_conflicts() {
  local timestamp
  timestamp="$(date +%Y%m%d-%H%M%S)"

  local backup_dir="$HOME/dotfiles-preinstall-backup-$timestamp"
  local moved_any=false

  for relative_path in "${MANAGED_PATHS[@]}"; do
    local target="$HOME/$relative_path"

    if [[ ! -e "$target" && ! -L "$target" ]]; then
      continue
    fi

    if path_is_managed_by_repo "$target"; then
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
  mkdir -p "$HOME/.config"

  local available_packages=()
  for package in "${STOW_PACKAGES[@]}"; do
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

main() {
  info "Dotfiles repository: $REPO_DIR"

  if [[ "$INSTALL_PACKAGES" == true ]]; then
    install_native_packages

    if [[ "$INSTALL_AUR" == true ]]; then
      install_aur_packages
    fi
  fi

  backup_conflicts
  stow_dotfiles

  if [[ ! -d /usr/share/themes/Gruvbox-Teal-Dark ]]; then
    warn "Gruvbox-Teal-Dark was not found under /usr/share/themes."
    warn "GTK 4 theme links will remain broken until that theme is installed."
  fi

  cat <<'DONE'

Installation complete.

Suggested follow-up:

  1. Start a new shell or run:
       exec bash -l

  2. Create local secrets that are intentionally not tracked:
       printf '%s\n' 'YOUR_OBS_WEBSOCKET_PASSWORD' > ~/bin/obs-password
       chmod 600 ~/bin/obs-password

  3. Review the local ytjukebox visualizer path:
       ~/.config/ytjukebox/playlist.toml

  4. Restart Hyprland or log out and back in after reviewing the config.
DONE
}

main "$@"
