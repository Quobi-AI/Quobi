# voice-type build & install.
#
#   make            help
#   make build      build the standalone binary -> dist/voice-type
#   make install    build (if needed) and install user-local, enable autostart
#   make reinstall  uninstall + install
#   make uninstall  remove the installation (keeps ~/.config/voice-type/)
#   make restart    kill running daemon and relaunch (use after `install`)
#   make dev        run from source (requires .venv)
#   make clean      remove build artifacts

SHELL := /bin/bash

PREFIX     ?= $(HOME)/.local
BIN_DIR     := $(PREFIX)/bin
APP_DIR     := $(PREFIX)/share/applications
ICON_DIR    := $(PREFIX)/share/icons/hicolor/scalable/apps
XDG_CFG     := $(if $(XDG_CONFIG_HOME),$(XDG_CONFIG_HOME),$(HOME)/.config)
CONFIG_DIR  := $(XDG_CFG)/voice-type
AUTO_DIR    := $(XDG_CFG)/autostart
STATE_DIR   := $(if $(XDG_STATE_HOME),$(XDG_STATE_HOME),$(HOME)/.local/state)/voice-type

# APP_NAME = the DEPRECATED built-in GUI menu entry (kept only so install/uninstall
# can delete any stale copy); DAEMON_NAME = autostart entry (runs the daemon).
BIN_NAME    := voice-type
APP_NAME    := voice-type.desktop
DAEMON_NAME := quobi-daemon.desktop
ICON_NAME   := voice-type.svg

PY          ?= python3
VENV        := .venv
VENV_PY     := $(VENV)/bin/python
VENV_PIP    := $(VENV)/bin/pip
PYINST      := $(VENV)/bin/pyinstaller

SOURCES     := $(wildcard voice_type/*.py) _entry.py voice-type.spec

.DEFAULT_GOAL := help

# ─── help ──────────────────────────────────────────────────────────────────

.PHONY: help
help:
	@echo "voice-type — build & install"
	@echo ""
	@echo "  make build           build the standalone binary -> dist/$(BIN_NAME)"
	@echo "  make install         install to $(PREFIX) and enable desktop autostart"
	@echo "  make reinstall       uninstall + install (overwrites everything cleanly)"
	@echo "  make restart         kill running daemon and relaunch from $(BIN_DIR)/$(BIN_NAME)"
	@echo "  make uninstall       remove the installation (config left alone)"
	@echo "  make dev             run from source via venv (no PyInstaller rebuild)"
	@echo "  make uinput-setup    install udev rule so voice-type can create a virtual kbd"
	@echo "  make ydotool-setup   install systemd unit for ydotoold (Wayland fallback)"
	@echo "  make clean           remove build/, dist/, and the PyInstaller workdir"
	@echo ""
	@echo "After install, the engine autostarts on login (the daemon). The front end"
	@echo "is the separate Quobi app; this binary has no GUI of its own."
	@echo "Logs land in $(STATE_DIR)/voice-type.log."

# ─── venv & build ──────────────────────────────────────────────────────────

$(VENV_PY):
	@echo "→ creating venv at $(VENV)"
	$(PY) -m venv $(VENV)
	$(VENV_PIP) install --upgrade pip wheel

# Sentinel target: re-runs pip install if either requirements file changes.
# Without this, a half-failed pip install (e.g. an incompatible version pin)
# leaves the venv in a broken state that make can't detect.
$(VENV)/.deps: $(VENV_PY) requirements.txt requirements-build.txt
	@echo "→ installing python deps"
	$(VENV_PIP) install -r requirements.txt
	$(VENV_PIP) install -r requirements-build.txt
	@touch $@

.PHONY: build
build: dist/$(BIN_NAME)

dist/$(BIN_NAME): $(VENV)/.deps $(SOURCES)
	@echo "→ building binary"
	$(PYINST) --clean --noconfirm voice-type.spec
	@echo "→ built dist/$(BIN_NAME) ($$(du -h dist/$(BIN_NAME) | cut -f1))"

# ─── install ───────────────────────────────────────────────────────────────

.PHONY: install
install: dist/$(BIN_NAME)
	@echo "→ installing to $(PREFIX)"
	@mkdir -p $(BIN_DIR) $(APP_DIR) $(ICON_DIR) $(CONFIG_DIR) $(AUTO_DIR) $(STATE_DIR)
	@install -m 0755 dist/$(BIN_NAME) $(BIN_DIR)/$(BIN_NAME)
	@install -m 0644 packaging/$(ICON_NAME) $(ICON_DIR)/$(ICON_NAME)
	@sed "s|@BIN@|$(BIN_DIR)/$(BIN_NAME)|g" packaging/$(DAEMON_NAME).in > $(AUTO_DIR)/$(DAEMON_NAME)
	@chmod 0644 $(AUTO_DIR)/$(DAEMON_NAME)
	# This binary is the engine only — Quobi is the front end. Remove any old
	# menu/autostart entries that launched the deprecated built-in GUI.
	@rm -f $(APP_DIR)/$(APP_NAME) $(AUTO_DIR)/$(APP_NAME)
	@if [ -f .env ] && [ ! -f $(CONFIG_DIR)/.env ]; then \
		install -m 0600 .env $(CONFIG_DIR)/.env; \
		echo "→ copied .env → $(CONFIG_DIR)/.env"; \
	fi
	@if [ ! -f $(CONFIG_DIR)/.env ]; then \
		echo "  ⚠ $(CONFIG_DIR)/.env missing — create it with: echo GROQ_API_KEY=sk_... > $(CONFIG_DIR)/.env && chmod 600 $(CONFIG_DIR)/.env"; \
	fi
	@command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database $(APP_DIR) 2>/dev/null || true
	@command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q -t -f $(PREFIX)/share/icons/hicolor 2>/dev/null || true
	@echo ""
	@echo "  ✔ engine    → $(BIN_DIR)/$(BIN_NAME)  (the dictation daemon — no GUI)"
	@echo "  ✔ autostart → $(AUTO_DIR)/$(DAEMON_NAME)  (runs the daemon on login)"
	@echo "  ✔ logs      → $(STATE_DIR)/voice-type.log"
	@echo ""
	@echo "  front end is Quobi (separate app). daemon:  $(BIN_NAME) --daemon"
	@echo "  (or: make restart for the daemon)"

.PHONY: reinstall
reinstall: uninstall install

.PHONY: uninstall
uninstall:
	@echo "→ removing installation (config kept at $(CONFIG_DIR))"
	@rm -f $(BIN_DIR)/$(BIN_NAME)
	@rm -f $(APP_DIR)/$(APP_NAME)
	@rm -f $(AUTO_DIR)/$(APP_NAME)
	@rm -f $(AUTO_DIR)/$(DAEMON_NAME)
	@rm -f $(ICON_DIR)/$(ICON_NAME)
	@command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database $(APP_DIR) 2>/dev/null || true
	@echo "  ✔ uninstalled"

# ─── runtime helpers ───────────────────────────────────────────────────────

.PHONY: restart
restart:
	@pkill -x $(BIN_NAME) 2>/dev/null && echo "→ killed running $(BIN_NAME)" || true
	@if [ ! -x $(BIN_DIR)/$(BIN_NAME) ]; then \
		echo "  ✘ $(BIN_DIR)/$(BIN_NAME) not found — run 'make install' first" >&2; exit 1; \
	fi
	@setsid $(BIN_DIR)/$(BIN_NAME) --daemon </dev/null >/dev/null 2>&1 &
	@sleep 0.8
	@if pgrep -x $(BIN_NAME) >/dev/null; then \
		echo "  ✔ started (logs: $(STATE_DIR)/voice-type.log)"; \
	else \
		echo "  ✘ daemon exited within 0.8s — last 25 lines of $(STATE_DIR)/voice-type.log:" >&2; \
		echo "  ───" >&2; \
		if [ -f $(STATE_DIR)/voice-type.log ]; then \
			tail -25 $(STATE_DIR)/voice-type.log | sed 's/^/    /' >&2; \
		else \
			echo "    (log file does not exist — try: $(BIN_DIR)/$(BIN_NAME))" >&2; \
		fi; \
		echo "  ───" >&2; \
		exit 1; \
	fi

.PHONY: dev
dev: $(VENV)/.deps
	$(VENV_PY) -m voice_type

# Run the cleanup-model regression battery through all three styles.
# Hits Groq directly (needs GROQ_API_KEY in ~/.config/voice-type/.env).
.PHONY: test-cleanup
test-cleanup: $(VENV)/.deps
	$(VENV_PY) tests/cleanup_regression.py

# ─── uinput setup (required for evdev grab+replay listener on Wayland) ────
# Grants the `input` group write access to /dev/uinput so voice-type can
# create a virtual keyboard. One-time setup; survives reboots.
.PHONY: uinput-setup
uinput-setup:
	@if [ ! -e /dev/uinput ]; then \
		echo "  ✘ /dev/uinput does not exist. Try: sudo modprobe uinput" >&2; \
		exit 1; \
	fi
	@echo "→ installing /etc/udev/rules.d/60-voice-type-uinput.rules (needs sudo)"
	sudo install -m 0644 packaging/uinput.rules /etc/udev/rules.d/60-voice-type-uinput.rules
	sudo udevadm control --reload-rules
	sudo udevadm trigger /dev/uinput
	@sleep 0.3
	@ls -la /dev/uinput
	@if id -nG | tr ' ' '\n' | grep -qx input; then \
		echo "  ✔ /dev/uinput accessible to the 'input' group (you're in it)"; \
		echo "    voice-type can now create its virtual keyboard for grab+replay."; \
	else \
		echo "  ⚠ you are not in the 'input' group. Run:"; \
		echo "      sudo usermod -aG input $$USER"; \
		echo "    and log out + back in."; \
	fi

# ─── ydotool setup (for Wayland compositors that don't expose virtual-keyboard) ──
# Required on GNOME Wayland and some KDE Plasma 6 builds. Installs a system-
# wide ydotoold service that listens on /tmp/.ydotool_socket (mode 0666).
.PHONY: ydotool-setup
ydotool-setup:
	@if ! command -v ydotoold >/dev/null; then \
		echo "✘ ydotool not installed. Install it first (e.g. 'sudo pacman -S ydotool')." >&2; exit 1; \
	fi
	@echo "→ installing /etc/systemd/system/ydotoold.service (needs sudo)"
	sudo install -m 0644 packaging/ydotoold.service /etc/systemd/system/ydotoold.service
	sudo systemctl daemon-reload
	sudo systemctl enable --now ydotoold
	@sleep 0.5
	@ls -la /tmp/.ydotool_socket 2>/dev/null && echo "  ✔ ydotoold running" \
		|| (echo "  ✘ socket not created — check 'systemctl status ydotoold'" >&2; exit 1)

# ─── clean ─────────────────────────────────────────────────────────────────

.PHONY: clean
clean:
	rm -rf build dist
	find . -type d -name __pycache__ -prune -exec rm -rf {} +

.PHONY: distclean
distclean: clean
	rm -rf $(VENV)
