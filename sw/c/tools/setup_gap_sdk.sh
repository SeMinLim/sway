#!/usr/bin/env bash
# Build the pinned GAP8 timing simulator without installing global packages.
set -euo pipefail

usage() {
	cat <<'EOF'
Usage: bash tools/setup_gap_sdk.sh DESTINATION

Requires Linux x86-64, bash, git, make, gcc/g++, rsync, and zlib development files.
Uses PYTHON_BIN (Python 3.10 or 3.11) when set, otherwise python3.10.
If neither is available, an existing uv installs Python 3.10.22 locally.
The destination receives the SDK, compiler, virtual environment, and env.sh.
EOF
}

if [[ ${1:-} == --help || ${1:-} == -h ]]; then
	usage
	exit 0
fi
if [[ $# -ne 1 ]]; then
	usage >&2
	exit 1
fi
if [[ $(uname -s) != Linux || $(uname -m) != x86_64 ]]; then
	echo 'The pinned prebuilt GAP compiler requires Linux x86-64.' >&2
	exit 1
fi
for executable in git make gcc g++ rsync; do
	command -v "$executable" >/dev/null || {
		echo "Missing prerequisite: $executable" >&2
		exit 1
	}
done
printf '#include <zlib.h>\nint main(void) { return zlibVersion() == 0; }\n' |
	gcc -x c - -lz -o /dev/null || {
		echo 'Install the zlib development package (Ubuntu: zlib1g-dev).' >&2
		exit 1
	}

mkdir -p -- "$1"
destination=$(cd -- "$1" && pwd)
if [[ "$destination" == *' '* ]]; then
	echo 'Choose a destination without spaces; the upstream SDK makefiles require it.' >&2
	exit 1
fi
sdkRevision=a23026507efe57410b98c5945544cff3150eb996
compilerRevision=e6b5fbbfbf57a0ad1092efb280d0417cf3ee7e51

checkoutPinned() {
	local repository=$1 revision=$2 directory=$3
	if [[ -e "$directory" ]]; then
		if [[ ! -d "$directory/.git" ]] ||
			[[ $(git -C "$directory" rev-parse HEAD) != "$revision" ]]; then
			echo "Existing directory does not contain the required revision: $directory" >&2
			exit 1
		fi
	else
		git init -q "$directory"
		git -C "$directory" remote add origin "$repository"
		git -C "$directory" fetch --depth 1 origin "$revision"
		git -C "$directory" checkout --detach FETCH_HEAD
	fi
}

checkoutPinned https://github.com/GreenWaves-Technologies/gap_sdk.git \
	"$sdkRevision" "$destination/gap_sdk"
checkoutPinned https://github.com/GreenWaves-Technologies/gap_riscv_toolchain_ubuntu.git \
	"$compilerRevision" "$destination/gap_riscv_toolchain_ubuntu"

pythonExecutable=${PYTHON_BIN:-python3.10}
if ! command -v "$pythonExecutable" >/dev/null; then
	if ! command -v uv >/dev/null; then
		echo 'Set PYTHON_BIN to Python 3.10/3.11, or install uv first.' >&2
		exit 1
	fi
	uv python install 3.10.22 --install-dir "$destination/python"
	pythonExecutable="$destination/python/cpython-3.10.22-linux-x86_64-gnu/bin/python3.10"
fi
"$pythonExecutable" -c 'import sys; assert sys.version_info[:2] in ((3, 10), (3, 11)), "GAP SDK needs Python 3.10/3.11: Python 3.12 removed imp"'
"$pythonExecutable" -m venv "$destination/venv"
"$destination/venv/bin/python" -m pip install \
	scons==4.9.1 cmake==3.31.10 pyelftools==0.33 prettytable==0.7.2 \
	pexpect==4.9.0 configparser==7.2.0 argcomplete==3.7.2 requests==2.34.2 \
	six==1.17.0 kconfiglib==14.1.0

# GCC 13 no longer supplies these integer typedefs through incidental headers.
# This changes host-side declarations only, not the simulated processor.
"$destination/venv/bin/python" - "$destination/gap_sdk" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
for filename, guard in (
    ("gap8/gvsoc/dpi-models/ext/nosv/svdpi.h", "#define __EXT_NOSV_SVDP_H__"),
    ("gap8/gvsoc/dpi-models/include/dpi/models.hpp", "#define __DPI_MODELS_HPP__"),
):
    path = root / filename
    source = path.read_text()
    if "#include <stdint.h>" not in source:
        if source.count(guard) != 1:
            raise RuntimeError(f"Unexpected pinned SDK header: {path}")
        path.write_text(source.replace(guard, guard + "\n\n#include <stdint.h>"))
PY

cat > "$destination/env.sh" <<'EOF'
# Source from bash before compiling or running GAP8 applications.
_sway_sdk_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$_sway_sdk_root/venv/bin/activate"
export GAP_RISCV_GCC_TOOLCHAIN="$_sway_sdk_root/gap_riscv_toolchain_ubuntu"
source "$_sway_sdk_root/gap_sdk/configs/gapuino_v3.sh"
unset _sway_sdk_root
EOF

# Upstream environment scripts expect some unset variables to expand empty.
set +u
source "$destination/env.sh"
cd "$destination/gap_sdk"
make -f gap8/gap8.mk gvsoc -j"${JOBS:-8}"
cd examples/gap8/basic/helloworld
make clean all run PMSIS_OS=pulpos platform=gvsoc
printf '\nSDK ready. Source this file before running Sway:\n%s/env.sh\n' "$destination"
