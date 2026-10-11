# Ubuntu 24.04 / 26.04 compatibility

Python 3.11+ is required. Use the distribution's supported Python/venv packages, then from the checkout:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install '.[plots]'
cp config.example.toml config.local.toml
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/rf-atlas --config config.local.toml --help
```

Choose an already installed RTL-SDR Blog V4L-compatible tool build in local TOML or PATH. An arbitrary distribution RTL build is not assumed to support this specific device. Verify its help, identification and bounded finite captures. USB permissions/driver conflicts may require an operator-managed setup; this application does not run sudo, install udev rules, unload kernel modules or start systemd units. Doctor reports acquisition failures instead of attempting repairs.

With no config, runtime files default to XDG_DATA_HOME/rf-atlas or the user's .local/share/rf-atlas. Windows drive paths are rejected on POSIX; use a native configuration. Tool names do not require .exe on Ubuntu. The code imports fcntl only in the POSIX adapter and never imports msvcrt or invokes WinDLL on Linux.

CI targets Ubuntu 24.04 and 26.04 and Python 3.11/3.13 without a receiver. Both runner labels are listed in the [GitHub runner-image inventory](https://github.com/actions/runner-images/blob/main/README.md). Ubuntu 24.04 software validation has run on an actual Linux host; see the release validation reports for measured versions and results. Linux receiver hardware and Ubuntu 26.04 remain separate validation requirements. CI has not been dispatched because nothing has been pushed.

Headless core replay/scanning needs NumPy/SciPy only. The plots extra uses Matplotlib's noninteractive Agg backend. Docker is optional and no image or service is required. Future systemd/container recipes must document USB access and use the same single-owner backend; neither is implemented here.

## First launch

```sh
.venv/bin/rf-atlas --config config.local.toml doctor
.venv/bin/rf-atlas --config config.local.toml app
```

Open the printed localhost address in a browser on the same machine. Keep the
process running while using the radio. A fresh installation starts idle; choose
manual tuning or explicitly enable Auto Survey. Ctrl+C shuts down the supervised
receiver processes and server. The same application controls, persistent private
profiles and opt-in identifier/frame retention described in the
[Windows first-launch guide](install-windows.md#first-launch-of-the-radio-application)
apply. No WSL, system service, remote receiver or external web service is needed.
