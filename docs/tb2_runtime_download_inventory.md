# TB2 Agent Runtime Download Inventory

这份清单逐题检查 Terminal-Bench 2 的 agent runtime 下载需求。每读完一个任务，就在这里写一条，避免批量规则漏判或误判。

范围只包括 agent 在 attempt 内可能触发的下载、安装、克隆、外部 API 或外部资源访问。`environment/`、`tests/`、verifier 依赖、Docker 基础镜像拉取不放进主判断。

每条记录包含：

- **Runtime need**：agent 做题时可能需要的外部依赖类型。
- **Evidence**：来自 `instruction.md`、`README.md`、`solution/` 的证据。
- **Routing**：更适合 PyPI cache、镜像源、预拉固定资源，还是 task-aware dynamic routing。
- **Judgment**：对当前工程优化的意义。

## Tasks

### `adaptive-rejection-sampler`

- **Runtime need**: APT package installation. The task explicitly asks the agent to install R if missing; the reference solution also installs `openssl` and `r-base-core`.
- **Exact runtime downloads**:
  - `apt`: `openssl`, then `r-base-core=4.3.3-2build2`; fallback path uses `r-base=4.3.3-2build2`.
  - Local-only inputs: `/protected/protected.tar.gz.enc` or `/app/protected.tar.gz.enc` are copied/decrypted locally and should not be counted as external downloads.
  - No `pip`, `git`, HuggingFace/model, dataset, or external API download is required.
- **Evidence**:
  - `instruction.md`: requires installing R if not already available and writing `/app/ars.R`.
  - `README.md`: lists R 4.3.3 as required and says it must be installed by the agent.
  - `solution/solve.sh`: runs `apt-get update`, `apt-get install -y openssl`, then installs `r-base-core=4.3.3-2build2` or falls back to `r-base=4.3.3-2build2`.
- **Routing**: APT cache/proxy for Ubuntu packages. No task-aware artifact routing is needed.
- **Judgment**: Network risk is mainly Ubuntu package availability. A generic APT mirror/cache helps; task-aware artifact routing is not needed.

### `bn-fit-modify`

- **Runtime need**: R/CRAN package installation.
- **Exact runtime downloads**:
  - `R/CRAN`: `remotes` from CRAN, then `remotes==2.5.0` and `bnlearn==4.9` via `remotes::install_version(...)`.
  - Local-only inputs: `/app/bn_sample_10k.csv`.
  - No APT, `pip`, Git, model, dataset, or external API download is required by the reference path.
- **Evidence**:
  - `instruction.md`: all task inputs are local, especially `/app/bn_sample_10k.csv`; no external dataset is requested.
  - `README.md`: mentions R packages such as `bnlearn`, with the dataset already pre-loaded.
  - `solution/solve.sh`: runs `install.packages("remotes")`, then `remotes::install_version("remotes", version = "2.5.0")` and `remotes::install_version("bnlearn", version = "4.9")`.
- **Routing**: CRAN package cache/R package mirror for `remotes` and `bnlearn`.
- **Judgment**: Runtime failure risk is CRAN/package installation, not benchmark data access.

### `break-filter-js-from-html`

- **Runtime need**: None confirmed.
- **Exact runtime downloads**:
  - `none`: no package manager, repository, model, dataset, or external API is used by the reference path.
  - Environment-provided tools: BeautifulSoup4, Selenium, Chromium/Chromedriver, and `/app/filter.py`.
- **Evidence**:
  - `instruction.md`: asks only to create `/app/out.html` that bypasses a local `/app/filter.py`.
  - `README.md`: installed browser/testing tools are part of the environment; the agent-facing task is local HTML/JavaScript construction.
  - `solution/solve.sh`: writes a crafted HTML payload directly; no package install, clone, download, or external API.
- **Routing**: No special runtime routing.
- **Judgment**: Network optimization is irrelevant for the agent attempt unless the model chooses to search/download external examples, which is not required by the task.

### `build-cython-ext`

- **Runtime need**: External Git clone plus PyPI package installation.
- **Exact runtime downloads**:
  - `git`: `https://github.com/SPOCKnots/pyknotid.git`, `--depth 1 --branch 0.5.3`, target `/app/pyknotid`.
  - `pip`: `setuptools==80.9.0`, `cython==3.1.3`.
  - Local/preinstalled dependency: `numpy==2.3.0` is already in the environment and should not be downgraded.
  - No external dataset, model, or API download is required.
- **Evidence**:
  - `instruction.md`: explicitly requires `git clone --depth 1 --branch 0.5.3 https://github.com/SPOCKnots/pyknotid.git` to `/app/pyknotid`.
  - `README.md`: says internet access is enabled for cloning the repository.
  - `solution/solve.sh`: performs the GitHub clone, then installs `setuptools==80.9.0` and `cython==3.1.3` with pip, builds extensions, and runs `pip install -e .`.
- **Routing**: Needs task-aware GitHub artifact cache/mirror for `SPOCKnots/pyknotid` at tag `0.5.3`, plus PyPI mirror for build tools.
- **Judgment**: This is not covered by generic PyPI cache alone because the source repository itself is required at runtime.

### `build-pmars`

- **Runtime need**: APT binary packages plus Debian source package download.
- **Exact runtime downloads**:
  - `apt source`: adds `deb-src http://deb.debian.org/debian stable main`, then runs `apt-get source pmars`.
  - `apt`: `build-essential=12.12`, `libncurses-dev=6.5+20250216-2`, `ca-certificates=20250419`, `dpkg-dev=1.22.21`, `devscripts=2.25.15+deb13u1`.
  - Local-only inputs: `/app/flashpaper.red`, `/app/rave.red`.
  - No GitHub, PyPI, model, dataset, or koth.org source download should be used.
- **Evidence**:
  - `instruction.md`: explicitly says koth.org is unreliable and instructs the agent to get pMARS source from Debian packages.
  - `README.md`: lists `apt-get source` as the package-management skill and says internet is enabled for Debian packages.
  - `solution/solve.sh`: adds a `deb-src` entry, runs `apt-get update`, installs build packages, then runs `apt-get source pmars`.
- **Routing**: APT cache/proxy must cover both binary package indexes/packages and Debian source packages.
- **Judgment**: This is generic Debian mirror/cache work, not GitHub or arbitrary artifact routing.

### `build-pov-ray`

- **Runtime need**: APT packages plus fixed external source archives.
- **Exact runtime downloads**:
  - `apt`: `build-essential=12.10ubuntu1`, `wget=1.21.4-1ubuntu4.1`, `ncompress=5.0-1`.
  - Fixed archives:
    - `https://www.povray.org/ftp/pub/povray/Old-Versions/Official-2.2/POVDOC.TAR.Z`
    - `https://www.povray.org/ftp/pub/povray/Old-Versions/Official-2.2/POVSCN.TAR.Z`
    - `https://www.povray.org/ftp/pub/povray/Old-Versions/Official-2.2/POVSRC.TAR.Z`
  - Local-only inputs: `/app/deps/illum1.pov`.
  - No Git, PyPI, model, dataset, or API download is required.
- **Evidence**:
  - `instruction.md`: asks the agent to find and download POV-Ray 2.2 source archives.
  - `README.md`: says internet access is required to download POV-Ray 2.2 source archives.
  - `solution/solve.sh`: installs build tools with APT, then downloads `POVDOC.TAR.Z`, `POVSCN.TAR.Z`, and `POVSRC.TAR.Z` from `https://www.povray.org/ftp/pub/povray/Old-Versions/Official-2.2/`.
- **Routing**: APT cache/proxy plus task-aware prefetch/cache for the three fixed POV-Ray archives.
- **Judgment**: Generic package cache is insufficient; the fixed legacy tarballs should be mirrored or preloaded because the task explicitly depends on them.

### `caffe-cifar-10`

- **Runtime need**: APT packages, external Git clone, and dataset download through Caffe's CIFAR-10 helper script.
- **Exact runtime downloads**:
  - `apt`: `build-essential`, `cmake`, `git`, `pkg-config`, `wget`, `libprotobuf-dev`, `libleveldb-dev`, `libsnappy-dev`, `libopencv-dev`, `libhdf5-serial-dev`, `protobuf-compiler`, `libatlas-base-dev`, `libboost-all-dev`, `libgflags-dev`, `libgoogle-glog-dev`, `liblmdb-dev`.
  - `git`: `https://github.com/BVLC/caffe.git`, then checkout commit `9b89154`.
  - Dataset/scripted download: `./data/cifar10/get_cifar10.sh` downloads CIFAR-10 through Caffe's bundled helper script.
  - Local build/preprocess: `./examples/cifar10/create_cifar10.sh`, `./examples/cifar10/train_quick.sh`.
  - No PyPI, HuggingFace/model, or external API download is required.
- **Evidence**:
  - `instruction.md`: requires cloning BVLC Caffe to `/app/caffe` and training on CIFAR-10.
  - `README.md`: says internet is required for cloning Caffe and downloading CIFAR-10.
  - `solution/solve.sh`: installs Caffe build dependencies with APT, runs `git clone https://github.com/BVLC/caffe.git`, checks out commit `9b89154`, then runs `./data/cifar10/get_cifar10.sh` and `./examples/cifar10/create_cifar10.sh`.
- **Routing**: APT cache/proxy, GitHub mirror/cache for BVLC Caffe, and task-aware cache for the CIFAR-10 download performed by Caffe's script.
- **Judgment**: This is a high-risk runtime-network task. Wheelhouse does not help; the critical paths are Debian/Ubuntu packages, GitHub, and dataset hosting used by the Caffe script.

### `cancel-async-tasks`

- **Runtime need**: None required by the task.
- **Exact runtime downloads**:
  - `none`: reference path uses only Python standard library `asyncio` and `typing`.
  - Optional package installation is allowed by the prompt but not needed.
- **Evidence**:
  - `instruction.md`: asks for a Python function in `/app/run.py` using system Python; it says packages may be installed if needed, but does not require any.
  - `README.md`: internet is enabled only as a possibility for package installation.
  - `solution/solve.sh`: writes `run.py` directly using Python standard-library `asyncio` and `typing`; no install or download.
- **Routing**: No special runtime routing.
- **Judgment**: Treat as no-download unless real trajectories show agents unnecessarily installing helper packages.

### `chess-best-move`

- **Runtime need**: APT package installation plus PyPI package installation.
- **Exact runtime downloads**:
  - `apt`: `stockfish`.
  - `pip`: `numpy==2.3.2`, `python-chess==1.2.0` with `--break-system-packages`.
  - Local-only inputs: `/app/chess_board.png`, `/fonts/noto.ttf`.
  - No Git, model, dataset, or external API download is required.
- **Evidence**:
  - `instruction.md`: only references local `chess_board.png` and asks for `/app/move.txt`.
  - `README.md`: suggests installing Stockfish and `python-chess`; the chess image is local.
  - `solution/solve.sh`: runs `apt install -y stockfish` and `pip3 install numpy==2.3.2 python-chess==1.2.0 --break-system-packages`.
- **Routing**: APT cache/proxy for Stockfish and PyPI mirror for Python packages.
- **Judgment**: No task-specific external data is needed; generic package caches cover the runtime network need.

### `circuit-fibsqrt`

- **Runtime need**: None confirmed.
- **Exact runtime downloads**:
  - `none`: reference path generates `/app/gates.txt` with a local Python script.
  - Environment-provided tools: `/app/sim.c`, Python 3, gcc/test tooling.
- **Evidence**:
  - `instruction.md`: asks the agent to create `/app/gates.txt` for a provided local simulator interface.
  - `README.md`: describes local Python/GCC tools and local verification.
  - `solution/solve.sh`: generates the circuit using an embedded Python script; no package install, clone, download, or external API command is present.
- **Routing**: No special runtime routing.
- **Judgment**: This task is compute/reasoning heavy rather than network dependent.

### `cobol-modernization`

- **Runtime need**: APT package installation.
- **Exact runtime downloads**:
  - `apt`: `gnucobol3`.
  - Local-only inputs: `/app/src/program.cbl`, `/app/src/INPUT.DAT`, `/app/data/ACCOUNTS.DAT`, `/app/data/BOOKS.DAT`, `/app/data/TRANSACTIONS.DAT`.
  - No PyPI, Git, model, dataset, or external API download is required.
- **Evidence**:
  - `instruction.md`: all inputs are local under `/app/src` and `/app/data`; the task is to reimplement local COBOL behavior in Python.
  - `README.md`: describes GnuCOBOL 3 as an installed/reference tool for compilation.
  - `solution/solve.sh`: runs `apt-get update && apt-get install -y gnucobol3`, then compiles the local COBOL program with `cobc`.
- **Routing**: APT cache/proxy for `gnucobol3`.
- **Judgment**: Runtime network risk is package installation only; no task-specific dataset or repository needs routing.

### `code-from-image`

- **Runtime need**: None required by the task.
- **Exact runtime downloads**:
  - `none`: reference path uses Python standard library `hashlib` and local file I/O.
  - Local-only input: `/app/code.png`.
  - Optional OCR/vision tooling is allowed by environment policy but not required by the task/reference path.
- **Evidence**:
  - `instruction.md`: uses local `/app/code.png` and asks for `/app/output.txt`.
  - `README.md`: internet is enabled for optional tools, but the required input image is already local.
  - `solution/solve.sh`: writes and runs a Python script using only the standard library `hashlib`; no install/download.
- **Routing**: No special runtime routing.
- **Judgment**: Agents may voluntarily install OCR tools, but the task does not require network access.

### `compile-compcert`

- **Runtime need**: APT packages, OPAM/Coq package downloads, and external Git clone.
- **Exact runtime downloads**:
  - `apt`: `opam`, `build-essential`, `unzip`, `rsync`, `git`, `ca-certificates`, `curl`, `m4`, `pkg-config`, `libgmp-dev`, `libzstd-dev`.
  - `opam`: switch `compcert-4.14` with `ocaml-base-compiler.4.14.2`; OPAM repo `https://coq.inria.fr/opam/released`; packages `coq.8.16.1`, `menhir.20230608`.
  - `git`: `https://github.com/AbsInt/CompCert.git`, `--depth 1 --branch v3.13.1`, target `/tmp/CompCert`.
  - No PyPI, HuggingFace/model, dataset, or external API download is required.
- **Evidence**:
  - `instruction.md`: requires freshly building CompCert 3.13.1 from source under `/tmp/CompCert/`.
  - `README.md`: says internet is required for downloading source and dependencies.
  - `solution/solve.sh`: installs system packages with APT, initializes OPAM, adds `https://coq.inria.fr/opam/released`, installs `coq.8.16.1` and `menhir.20230608`, then clones `https://github.com/AbsInt/CompCert.git` at tag `v3.13.1`.
- **Routing**: APT cache/proxy, OPAM repository/package cache, and GitHub mirror/cache for AbsInt/CompCert.
- **Judgment**: High runtime-network risk. This needs task-aware routing beyond generic Python PyPI cache.

### `configure-git-webserver`

- **Runtime need**: APT package installation.
- **Exact runtime downloads**:
  - `apt`: `git`, `nginx`, `openssh-server`.
  - Local service operations: `git clone git@localhost:/git/server` and `curl http://localhost:8080/hello.html` are benchmark-internal checks, not external downloads.
  - No PyPI, external Git repository, model, dataset, or external API download is required.
- **Evidence**:
  - `instruction.md`: `git clone user@server:/git/server` and `curl http://server:8080/hello.html` refer to the local service the agent must configure, not an external repository or external URL.
  - `README.md`: task requirements use `git@localhost:/git/server` and `http://localhost:8080/hello.html`.
  - `solution/solve.sh`: installs `git`, `nginx`, and `openssh-server` with APT, then creates a local bare repository, SSH service, hook, and nginx config.
- **Routing**: APT cache/proxy only.
- **Judgment**: Do not classify this as external GitHub/Git download. The Git operations are local benchmark behavior; only package installation needs optimization.

### `constraints-scheduling`

- **Runtime need**: None confirmed.
- **Exact runtime downloads**:
  - `none`: reference path uses Python standard library `re`, `datetime`, and local file I/O.
  - Local-only inputs: `/app/alice_calendar.ics`, `/app/bob_calendar.ics`, `/app/carol_calendar.ics`.
  - No APT, PyPI, Git, model, dataset, or external API download is required.
- **Evidence**:
  - `instruction.md`: all inputs are local ICS files under `/app`; output is `/app/meeting_scheduled.ics`.
  - `README.md`: describes local Python/date-time/ICS parsing; no required package or external data.
  - `solution/solve.sh`: runs an inline Python script using standard libraries only.
- **Routing**: No special runtime routing.
- **Judgment**: This task should not be affected by network except for unnecessary agent-initiated package installs.

### `count-dataset-tokens`

- **Runtime need**: PyPI package installation plus HuggingFace dataset/model tokenizer download.
- **Exact runtime downloads**:
  - `pip`: `datasets==4.0.0`, `transformers==4.56.0`, `jinja2==3.1.6`.
  - `HuggingFace model/tokenizer`: `Qwen/Qwen2.5-1.5B-Instruct` via `AutoTokenizer.from_pretrained(..., trust_remote_code=True)`.
  - `HuggingFace dataset`: `ryanmarten/OpenThoughts-1k-sample`, config `metadata`, split `train`.
  - No APT, Git, or non-HF external API download is required.
- **Evidence**:
  - `instruction.md`: explicitly asks for `ryanmarten/OpenThoughts-1k-sample` on HuggingFace and the `Qwen2.5-1.5B-Instruct` tokenizer.
  - `README.md`: says internet access is required to download the dataset and tokenizer; required packages are `datasets`, `transformers`, and `jinja2`.
  - `solution/solve.sh`: installs `datasets==4.0.0 transformers==4.56.0 jinja2==3.1.6`, calls `AutoTokenizer.from_pretrained("Qwen/Qwen2.5-1.5B-Instruct")`, and `load_dataset("ryanmarten/OpenThoughts-1k-sample", "metadata")`.
- **Routing**: Wheelhouse/PyPI mirror plus HuggingFace cache/prepull for both dataset and tokenizer.
- **Judgment**: High runtime-network risk; this needs HF-aware caching, not just generic package mirrors.

### `crack-7z-hash`

- **Runtime need**: APT package installation.
- **Exact runtime downloads**:
  - `apt`: `libcompress-raw-lzma-perl`, `7zip`.
  - Local/preinstalled tools: `/app/john/run/7z2john.pl`, `/app/john/run/john`.
  - Local-only input: `/app/secrets.7z`.
  - No PyPI, Git, model, dataset, or external API download is required.
- **Evidence**:
  - `instruction.md`: uses local `secrets.7z` and asks for `/app/solution.txt`.
  - `README.md`: John the Ripper is pre-compiled in the environment; the archive is local.
  - `solution/solve.sh`: runs `apt-get update`, installs `libcompress-raw-lzma-perl`, and installs `7zip`; no external data or clone.
- **Routing**: APT cache/proxy for Perl LZMA support and 7zip.
- **Judgment**: This is exactly the kind of task that fails under poor APT/disk/network conditions even though the task data is local.

### `custom-memory-heap-crash`

- **Runtime need**: None confirmed.
- **Exact runtime downloads**:
  - `none`: reference path only compiles and debugs local C++ files with preinstalled toolchains.
  - Local-only inputs/tools: `/app/main.cpp`, `/app/user.cpp`, `/app/user.h`, custom libstdc++ under `/usr/local/gcc-custom-{debug,release}/`, source tree under `/build/`, local `g++`, `gdb`, and Valgrind/test tooling.
  - No APT, PyPI, Git, model, dataset, or external API download is required.
- **Evidence**:
  - `instruction.md`: all code, compiler paths, and custom libstdc++ source are local (`/app`, `/usr/local/gcc-custom-*`, `/build`).
  - `README.md`: debugging/build tools are part of the environment.
  - `solution/solve.sh`: compiles local files, runs local binaries/GDB, and rewrites `/app/user.cpp`; no install/download/clone/API call.
- **Routing**: No special runtime routing.
- **Judgment**: This task depends on local debugging and build environment consistency, not network.

### `db-wal-recovery`

- **Runtime need**: None confirmed.
- **Exact runtime downloads**:
  - `none`: reference path uses local `sqlite3`, `xxd`, Python standard library `sqlite3/json`, and local files.
  - Local-only inputs: `/app/main.db`, `/app/main.db-wal`.
  - No APT, PyPI, Git, model, dataset, or external API download is required.
- **Evidence**:
  - `instruction.md`: database and WAL files are local under `/app`.
  - `README.md`: required tools are Python 3, SQLite3, xxd, and pip in the environment; no external data is requested.
  - `solution/solve.sh`: reads local DB/WAL files, writes local Python scripts, decrypts WAL locally, and queries SQLite; no install/download/clone/API call.
- **Routing**: No special runtime routing.
- **Judgment**: Network is not part of the intended solution path.

### `distribution-search`

- **Runtime need**: None confirmed.
- **Exact runtime downloads**:
  - `none`: reference path uses preinstalled NumPy/SciPy/Python and writes `/app/dist.npy`.
  - No APT, PyPI, Git, model, dataset, or external API download is required.
- **Evidence**:
  - `instruction.md`: says NumPy and SciPy are provided and asks for local `/app/dist.npy`.
  - `README.md`: lists NumPy/SciPy as pre-installed libraries.
  - `solution/solve.sh`: writes and runs a local Python numerical search script; no package install, clone, download, or external API.
- **Routing**: No special runtime routing.
- **Judgment**: This task is numerical optimization, not network dependent.

### `dna-assembly`

- **Runtime need**: APT package installation.
- **Exact runtime downloads**:
  - `apt`: `emboss`, `primer3`.
  - Local-only input: `/app/sequences.fasta`.
  - No PyPI, Git, model, dataset, or external API download is required.
- **Evidence**:
  - `instruction.md`: all sequences are in local `sequences.fasta`; asks for local `primers.fasta`.
  - `README.md`: required tools are EMBOSS and primer3 and must be installed by the agent.
  - `solution/solve.sh`: runs `apt-get update` and `apt-get install -y emboss primer3`, then uses local `needle` and `oligotm`.
- **Routing**: APT cache/proxy for EMBOSS and primer3.
- **Judgment**: Package installation is the only intended network dependency.

### `dna-insert`

- **Runtime need**: APT package installation.
- **Exact runtime downloads**:
  - `apt`: `emboss`, `primer3`.
  - Local-only input: `/app/sequences.fasta`.
  - No PyPI, Git, model, dataset, or external API download is required.
- **Evidence**:
  - `instruction.md`: uses local `sequences.fasta` and asks for local `primers.fasta`.
  - `README.md`: says tools such as `primer3` and `emboss` are required.
  - `solution/solve.sh`: runs `apt-get update` and `apt-get install -y emboss primer3`, then uses local sequence alignment and `oligotm`.
- **Routing**: APT cache/proxy for EMBOSS and primer3.
- **Judgment**: Same network profile as `dna-assembly`: package installation only, no external biological database or dataset access.

### `extract-elf`

- **Runtime need**: None confirmed.
- **Exact runtime downloads**:
  - `none`: reference path uses preinstalled Node.js and built-in `fs` only.
  - Local-only input: `/app/a.out`; verifier may compile local test binaries with preinstalled gcc.
  - No APT, npm, PyPI, Git, model, dataset, or external API download is required.
- **Evidence**:
  - `instruction.md`: uses local `/app/a.out` and asks for local `extract.js`.
  - `README.md`: Node.js, npm, and gcc are installed in the environment.
  - `solution/solve.sh`: writes a standalone Node.js ELF parser using the built-in `fs` module; no `npm install`, APT, clone, download, or API call.
- **Routing**: No special runtime routing.
- **Judgment**: Network is not part of the intended solution.

### `extract-moves-from-video`

- **Runtime need**: Fixed external video download, and potentially video/OCR tooling depending on agent strategy.
- **Exact runtime downloads**:
  - Fixed video/resource: `https://www.youtube.com/watch?v=ZCbvyPbhRfA`.
  - Likely agent-chosen tooling if not preinstalled: video downloader such as `yt-dlp`, video processing such as `ffmpeg`, and OCR/transcription packages/tools. The official prompt does not pin these package names.
  - Reference shortcut: `solution/solve.sh` hard-codes `/app/solution.txt` and performs no download, but that is not the intended task path.
  - No fixed HuggingFace/model or biological/API resource is specified by the task.
- **Evidence**:
  - `instruction.md`: explicitly says to download `https://www.youtube.com/watch?v=ZCbvyPbhRfA`.
  - `README.md`: repeats the YouTube URL and says internet access is required for downloading the video; likely tools include `yt-dlp`, `ffmpeg`, and OCR/transcription libraries.
  - `solution/solve.sh`: hard-codes the expected move list and does not download, but that is a reference shortcut rather than the task's intended runtime path.
- **Routing**: Task-aware fixed-resource cache for the YouTube video is the most important optimization; package caches may also help if agents install `yt-dlp`, `ffmpeg`, or OCR tools.
- **Judgment**: High runtime-network risk. This is one of the clearest cases where a task-specific artifact cache is needed.

### `feal-differential-cryptanalysis`

- **Runtime need**: None confirmed.
- **Exact runtime downloads**:
  - `none`: reference path writes `/app/attack.py` and uses local Python only.
  - Local-only input: `/app/feal.py`.
  - No APT, PyPI, Git, model, dataset, or external API download is required.
- **Evidence**:
  - `instruction.md`: uses local `/app/feal.py` and asks for local `/app/attack.py`.
  - `README.md`: encryption implementation is provided locally; no external data or package is required.
  - `solution/solve.sh`: writes pure Python attack code; no install/download/clone/API call.
- **Routing**: No special runtime routing.
- **Judgment**: Network should not affect the intended attack path.

### `feal-linear-cryptanalysis`

- **Runtime need**: None confirmed.
- **Exact runtime downloads**:
  - `none`: reference path writes local C attack code and uses preinstalled gcc/Python.
  - Local-only inputs: `/app/feal.c`, `/app/decrypt.c`, `/app/pairs.txt`, `/app/ciphertexts.txt`.
  - No APT, PyPI, Git, model, dataset, or external API download is required.
- **Evidence**:
  - `instruction.md`: all inputs are local (`feal.c`, `decrypt.c`, `pairs.txt`, `ciphertexts.txt`).
  - `README.md`: GCC and Python are available in the environment.
  - `solution/solve.sh`: writes `attack.c`, compiles it with local `gcc`, and runs local decrypt logic; no install/download/clone/API call.
- **Routing**: No special runtime routing.
- **Judgment**: Network is irrelevant to the intended solution path.

### `filter-js-from-html`

- **Runtime need**: None confirmed.
- **Exact runtime downloads**:
  - `none`: reference path writes `/app/filter.py` and imports environment-provided `bs4`.
  - Environment-provided tools: BeautifulSoup4, Chromium, Selenium.
  - No APT, PyPI, Git, model, dataset, or external API download is required by the agent path.
- **Evidence**:
  - `instruction.md`: asks to create local `/app/filter.py`.
  - `README.md`: Chromium, Selenium, and BeautifulSoup are installed in the environment for verification/use.
  - `solution/solve.sh`: writes a Python script using `bs4`; no package install, clone, download, or external API.
- **Routing**: No special runtime routing.
- **Judgment**: Intended solution does not require network. Optional agent package installs would be self-inflicted.

### `financial-document-processor`

- **Runtime need**: APT package installation plus Python package resolution through `uv`.
- **Exact runtime downloads**:
  - `apt`: `tesseract-ocr=5.3.4-1build5`.
  - `uv/PyPI`: `numpy==2.3.2`, `opencv-python==4.11.0.86`, `packaging==25.0`, `pandas==2.3.2`, `pillow==11.3.0`, `pymupdf==1.25.2`, `pytesseract==0.3.13`, `python-dateutil==2.9.0.post0`, `pytz==2025.2`, `six==1.17.0`, `tzdata==2025.2`.
  - Local-only inputs: randomized JPG/PDF files under `/app/documents/`.
  - No Git, model, external dataset, or external API download is required.
- **Evidence**:
  - `instruction.md`: input JPG/PDF documents are local under `/app/documents/`.
  - `README.md`: says Tesseract OCR must be installed by the agent and lists Python libraries such as `pytesseract`, PIL, PyMuPDF, and pandas.
  - `solution/solve.sh`: writes a Python script with inline `uv` dependencies including `opencv-python`, `pandas`, `pillow`, `pymupdf`, and `pytesseract`, installs `tesseract-ocr=5.3.4-1build5` with APT, then runs `uv run document_processor.py`.
- **Routing**: APT cache/proxy for Tesseract and Python PyPI mirror compatible with `uv`.
- **Judgment**: Runtime network risk is package installation, not document download. Available package access should be checked against these OCR/PDF packages.

### `fix-code-vulnerability`

- **Runtime need**: None confirmed.
- **Exact runtime downloads**:
  - `none`: reference path edits local `/app/bottle.py` and writes `/app/report.jsonl`.
  - Environment-provided tools: git, pytest, sed.
  - Local-only repository: Bottle source is already under `/app`.
  - No APT, PyPI, Git clone, model, dataset, or external API download is required.
- **Evidence**:
  - `instruction.md`: repository is already local in `/app`; asks to edit `/app/bottle.py` and write `/app/report.jsonl`.
  - `README.md`: tools such as git, sed, and pytest are available in the environment.
  - `solution/solve.sh`: inspects local files and patches `bottle.py` with `sed`; no install/download/clone/API call.
- **Routing**: No special runtime routing.
- **Judgment**: Intended solution is local code review and edit.

### `fix-git`

- **Runtime need**: None confirmed.
- **Exact runtime downloads**:
  - `none`: reference path uses local `.git/logs/HEAD`, local branches, and local merge.
  - Local-only repository: `/app/personal-site`.
  - No APT, PyPI, external Git fetch/clone, model, dataset, or external API download is required.
- **Evidence**:
  - `instruction.md`: asks to recover local changes after checkout.
  - `README.md`: repository is already local at `/app/personal-site`; Git is installed.
  - `solution/solve.sh`: reads `.git/logs/HEAD`, creates a local recovery branch, checks out master, and merges; no external clone/fetch/download.
- **Routing**: No special runtime routing.
- **Judgment**: This is purely local Git history recovery.

### `fix-ocaml-gc`

- **Runtime need**: None confirmed for the agent attempt.
- **Exact runtime downloads**:
  - `none` for the agent attempt: reference path only edits `/app/ocaml/runtime/shared_heap.c`.
  - Local-only repository: broken OCaml tree under `/app/ocaml`.
  - Verifier may re-clone original OCaml for clean tests, but that is verifier-side and outside this agent-runtime inventory.
  - No APT, PyPI, agent-side Git clone, model, dataset, or external API download is required by the reference path.
- **Evidence**:
  - `instruction.md`: asks to debug the local OCaml tree and run local tests.
  - `README.md`: the broken OCaml repository is already pre-cloned; the note about re-cloning original OCaml belongs to verification, not agent runtime.
  - `solution/solve.sh`: applies a local `sed` edit to `/app/ocaml/runtime/shared_heap.c`; no package install, clone, download, or external API.
- **Routing**: No special runtime routing for the agent. Verifier-side clone, if any, is outside this inventory's main scope.
- **Judgment**: Keep this classified as local source debugging, not runtime network.

### `gcode-to-text`

- **Runtime need**: APT package installation plus PyPI package installation.
- **Exact runtime downloads**:
  - `apt`: `python3-opencv`, `tesseract-ocr`, `libtesseract-dev`.
  - `pip`: `matplotlib==3.10.7`, `pytesseract==0.3.13`, `pillow==12.0.0`, `opencv-python==4.12.0.88`.
  - Local-only input: `/app/text.gcode`.
  - No Git, model, external dataset, or external API download is required.
- **Evidence**:
  - `instruction.md`: input `text.gcode` is local.
  - `README.md`: describes rendering and OCR; internet is allowed but the data is local.
  - `solution/solve.sh`: installs `python3-opencv`, `tesseract-ocr`, and `libtesseract-dev` with APT, and installs `matplotlib`, `pytesseract`, `pillow`, and `opencv-python` with pip.
- **Routing**: APT cache/proxy plus PyPI mirror for image/OCR packages.
- **Judgment**: Runtime network risk is dependency installation, not task data access.

### `git-leak-recovery`

- **Runtime need**: None confirmed.
- **Exact runtime downloads**:
  - `none`: reference path uses local Git object database and local `git fsck/reflog/gc`.
  - Local-only repository: `/app/repo`.
  - No APT, PyPI, external Git fetch/clone, model, dataset, or external API download is required.
- **Evidence**:
  - `instruction.md`: repository is local at `/app/repo`; secret must be recovered and cleaned locally.
  - `README.md`: Git is pre-installed.
  - `solution/solve.sh`: uses local `git fsck`, `git show`, `git reflog expire`, and `git gc`; no external fetch/clone/download.
- **Routing**: No special runtime routing.
- **Judgment**: This is local Git forensics.

### `git-multibranch`

- **Runtime need**: None confirmed.
- **Exact runtime downloads**:
  - `none`: reference path uses preinstalled `nginx`, `openssh-server`, `git`, `openssl`, Python, and local service configuration.
  - Local service traffic: `git@localhost:/git/project` and `https://localhost:8443/...` are benchmark-internal.
  - No APT, PyPI, external Git fetch/clone, model, dataset, or external API download is required.
- **Evidence**:
  - `instruction.md`: Git endpoint is `git@localhost:/git/project`; HTTPS endpoints are `https://localhost:8443/...`.
  - `README.md`: nginx, openssh-server, git, openssl, and Python are pre-installed; SSL/nginx are pre-configured.
  - `solution/solve.sh`: creates local users, local bare repo, local hooks, starts local sshd/nginx; no package install or external network access.
- **Routing**: No special runtime routing.
- **Judgment**: Local service setup only. Do not classify localhost Git/HTTPS traffic as external download.

### `gpt2-codegolf`

- **Runtime need**: None confirmed.
- **Exact runtime downloads**:
  - `none`: reference path writes dependency-free `/app/gpt2.c`.
  - Local/pre-downloaded model artifacts: `/app/gpt2-124M.ckpt`, `/app/vocab.bpe`.
  - Environment-provided tool: gcc.
  - No APT, PyPI, Git, HuggingFace/model download, dataset, or external API download is required.
- **Evidence**:
  - `instruction.md`: says GPT-2 weights are already downloaded as `gpt2-124M.ckpt` and the program should read the local `.ckpt` and `.bpe`.
  - `README.md`: confirms GPT-2 124M model weights and vocabulary are pre-downloaded.
  - `solution/solve.sh`: writes a dependency-free C file; no package install, model download, clone, or API call.
- **Routing**: No special runtime routing.
- **Judgment**: Do not route this through HuggingFace/model cache; the benchmark already provides model artifacts locally.

### `headless-terminal`

- **Runtime need**: None confirmed.
- **Exact runtime downloads**:
  - `none`: reference path copies `/solution/headless_terminal.py` to `/app/headless_terminal.py`.
  - Environment-provided tools: tmux, screen, expect, Python.
  - No APT, PyPI, Git, model, dataset, or external API download is required by the reference path.
- **Evidence**:
  - `instruction.md`: asks to implement `/app/headless_terminal.py`; it says install dependencies into system Python, but the needed terminal tools are provided.
  - `README.md`: tmux, screen, and expect are pre-installed.
  - `solution/solve.sh`: copies `/solution/headless_terminal.py` to `/app/headless_terminal.py`; implementation uses Python standard library and tmux.
- **Routing**: No special runtime routing.
- **Judgment**: No intended package or external resource download.

### `hf-model-inference`

- **Runtime need**: HuggingFace model/tokenizer download.
- **Exact runtime downloads**:
  - `HuggingFace model/tokenizer`: `distilbert-base-uncased-finetuned-sst-2-english` via `AutoModelForSequenceClassification.from_pretrained(...)` and `AutoTokenizer.from_pretrained(...)`.
  - Save target: `/app/model_cache/sentiment_model`.
  - Environment-preinstalled packages: `transformers==4.56.0`, `torch==2.7.1`, `flask==3.1.1`.
  - No APT, PyPI, Git, external dataset, or non-HF API download is required.
- **Evidence**:
  - `instruction.md`: explicitly requires downloading `distilbert-base-uncased-finetuned-sst-2-english` from Hugging Face into `/app/model_cache/sentiment_model`.
  - `README.md`: transformers, torch, and flask are pre-installed; internet is enabled for model download.
  - `solution/solve.sh`: calls `AutoModelForSequenceClassification.from_pretrained(model_name)` and `AutoTokenizer.from_pretrained(model_name)`, then saves both locally.
- **Routing**: HuggingFace cache/prepull for `distilbert-base-uncased-finetuned-sst-2-english`.
- **Judgment**: Package PyPI cache is not the main issue; model artifact caching is required for reliable runs.

### `install-windows-3.11`

- **Runtime need**: Fixed external source archive download for QEMU 5.2.0; optional external IP lookup in the reference script.
- **Exact runtime downloads**:
  - Fixed archive: `https://download.qemu.org/qemu-5.2.0.tar.xz`, only if `/usr/local/bin/qemu-system-i386` is absent.
  - Optional external IP lookup endpoints used only for display/help text: `ifconfig.me`, `ipecho.net/plain`, `icanhazip.com`, `ipinfo.io/ip`.
  - Local-only input: `/app/isos/win311.img`.
  - No PyPI, Git, HuggingFace/model, external dataset, or external API is required for the VM semantics.
- **Evidence**:
  - `instruction.md`: Windows image is local at `/app/isos/win311.img`; QEMU 5.2.0 compatibility is required.
  - `README.md`: required tools include QEMU 5.2.0, VNC, nginx, websockify, and socat; internet is allowed for dependencies.
  - `solution/solve.sh`: if `/usr/local/bin/qemu-system-i386` is absent, downloads `https://download.qemu.org/qemu-5.2.0.tar.xz`, builds it, and installs it. The script also tries public IP services (`ifconfig.me`, `ipecho.net/plain`, `icanhazip.com`, `ipinfo.io/ip`) only for display/help text.
- **Routing**: Task-aware cache for the QEMU 5.2.0 tarball. External IP lookup can be removed or ignored because it does not affect benchmark correctness.
- **Judgment**: Critical dependency is the fixed QEMU source archive, not the Windows image, which is already local.

### `kv-store-grpc`

- **Runtime need**: PyPI package installation.
- **Exact runtime downloads**:
  - `pip`: `grpcio==1.73.0`, `grpcio-tools==1.73.0`.
  - Local generated files: `/app/kv-store.proto`, generated `kv_store_pb2.py`, `kv_store_pb2_grpc.py`, `/app/server.py`.
  - No APT, Git, model, dataset, or external API download is required.
- **Evidence**:
  - `instruction.md`: explicitly requires installing `grpcio==1.73.0` and `grpcio-tools==1.73.0` system-wide.
  - `README.md`: internet is enabled for pip installations.
  - `solution/solve.sh`: runs `pip install grpcio==1.73.0 grpcio-tools==1.73.0`, then generates local proto bindings and starts the server.
- **Routing**: Wheelhouse/PyPI mirror for pinned gRPC packages.
- **Judgment**: Generic Python package cache is enough; no task-specific artifact or dataset.

### `large-scale-text-editing`

- **Runtime need**: None confirmed.
- **Exact runtime downloads**:
  - `none`: reference path writes `/app/apply_macros.vim` and uses preinstalled Vim.
  - Local-only input: `/app/input.csv`; expected output is local/test-generated.
  - No APT, PyPI, Git, model, dataset, or external API download is required.
- **Evidence**:
  - `instruction.md`: uses local `/app/input.csv` and writes `/app/apply_macros.vim`.
  - `README.md`: Vim and Python are available in the environment.
  - `solution/solve.sh`: writes a Vimscript only; no package install, clone, download, or API call.
- **Routing**: No special runtime routing.
- **Judgment**: Runtime cost is local text processing, not network.

### `largest-eigenval`

- **Runtime need**: PyPI package installation in the reference solution; task allows optional package installs.
- **Exact runtime downloads**:
  - `pip`: `eigenpy==3.10.3`.
  - Local/preinstalled dependency: `numpy==2.3.0`.
  - Local-only files: `/app/eigen.py`, `/app/eval.py`.
  - No APT, Git, model, dataset, or external API download is required.
- **Evidence**:
  - `instruction.md`: input and evaluation are local, but it explicitly allows installing system-wide Python packages or other languages.
  - `README.md`: NumPy is pre-installed; internet is enabled for additional packages.
  - `solution/solve.sh`: runs `pip install eigenpy==3.10.3`, then writes `/app/eigen.py`.
- **Routing**: Wheelhouse/PyPI mirror for `eigenpy` and its dependencies.
- **Judgment**: No external data is required, but reliable package installation matters if agents follow the package-based optimization path.

### `llm-inference-batching-scheduler`

- **Runtime need**: None confirmed.
- **Exact runtime downloads**:
  - `none`: reference path writes local planner code and reads local JSONL/cost-model files.
  - Local-only inputs: `/app/task_file/input_data/requests_bucket_1.jsonl`, `/app/task_file/input_data/requests_bucket_2.jsonl`, `/app/task_file/scripts/cost_model.py`, `/app/task_file/scripts/baseline_packer.py`.
  - No APT, PyPI, Git, model, dataset, or external API download is required.
- **Evidence**:
  - `instruction.md`: all request files and helper scripts are local under `/app/task_file`.
  - `README.md`: provided `cost_model.py` and `baseline_packer.py` are local.
  - `solution/solve.sh`: writes and runs a local Python planner that imports `/app/task_file/scripts/cost_model.py`; no install/download/clone/API call.
- **Routing**: No special runtime routing.
- **Judgment**: This is local algorithmic optimization; network should not be involved.

### `log-summary-date-ranges`

- **Runtime need**: APT package installation in the reference solution, although the tools are normally base utilities.
- **Exact runtime downloads**:
  - `apt`: `grep`, `coreutils`.
  - Local-only inputs: files under `/app/logs`; fixed reference date `2025-08-12`.
  - No PyPI, Git, model, dataset, or external API download is required.
- **Evidence**:
  - `instruction.md`: all logs are local under `/app/logs`; current date is fixed in the prompt.
  - `README.md`: pre-generated logs are local.
  - `solution/solve.sh`: runs `apt-get update && apt-get install -y grep coreutils`, then uses local shell tools to produce `/app/summary.csv`.
- **Routing**: APT cache/proxy if agents follow the reference install step.
- **Judgment**: No task-specific data download. Package install is probably avoidable but should be covered by generic APT cache.

### `mailman`

- **Runtime need**: None confirmed.
- **Exact runtime downloads**:
  - `none`: reference path configures preinstalled postfix/mailman3 locally.
  - Environment-provided tools: postfix, mailutils, mailman3, Python.
  - Local-only helper: `/app/eval.py`.
  - No APT, PyPI, Git, model, dataset, or external API download is required.
- **Evidence**:
  - `instruction.md`: postfix and mailman3 are already installed.
  - `README.md`: postfix, mailutils, and mailman3 are pre-installed; `/app/eval.py` is local.
  - `solution/solve.sh`: configures local postfix/mailman files, starts local services, creates the list, and runs `/app/eval.py`; no package install, clone, download, or external API.
- **Routing**: No special runtime routing.
- **Judgment**: Local service configuration only.

### `make-doom-for-mips`

- **Runtime need**: APT package installation for cross-compilation tools.
- **Exact runtime downloads**:
  - `apt`: `clang`, `gcc-mips-linux-gnu`, `g++-mips-linux-gnu`, `llvm`, `llvm-dev`, `lld`.
  - Local-only inputs: `/app/doomgeneric/`, `/app/doomgeneric_img.c`, `/app/vm.js`, `/app/doom1.wad`.
  - No PyPI, Git clone, model, dataset, or external API download is required.
- **Evidence**:
  - `instruction.md`: DOOM source, custom backend, emulator, and WAD are local under `/app`.
  - `README.md`: Node.js, git, make, clang, and MIPS GNU toolchain are described as pre-installed/key tools, but the reference solution still installs toolchain packages.
  - `solution/solve.sh`: applies a large local patch/build setup and runs `apt install -y clang gcc-mips-linux-gnu g++-mips-linux-gnu llvm llvm-dev lld`.
- **Routing**: APT cache/proxy for LLVM/Clang and MIPS cross toolchain packages.
- **Judgment**: No task-specific external source/data download is required; package installation is the runtime network risk.

### `make-mips-interpreter`

- **Runtime need**: None confirmed.
- **Exact runtime downloads**:
  - `none`: reference path writes local `vm.js` and uses preinstalled Node.js.
  - Local-only inputs: `/app/doomgeneric_mips`, `/app/doomgeneric/`, `/app/doom1.wad`, expected/reference files used by tests.
  - No APT, npm, PyPI, Git, model, dataset, or external API download is required.
- **Evidence**:
  - `instruction.md`: MIPS ELF, DOOM source, WAD, and expected files are local under `/app`.
  - `README.md`: Node.js and other verification tools are part of the environment.
  - `solution/solve.sh`: writes a standalone `vm.js`; no package install, clone, download, or external API.
- **Routing**: No special runtime routing.
- **Judgment**: This task is local systems implementation.

### `mcmc-sampling-stan`

- **Runtime need**: APT package installation plus CRAN/R package installation.
- **Exact runtime downloads**:
  - `apt`: `gfortran=4:13.2.0-7ubuntu1`, `liblapack-dev=3.12.0-3build1.1`, `libblas-dev=3.12.0-3build1.1`.
  - `R/CRAN`: `remotes`, then `Rcpp==1.1.0`, `RcppEigen==0.3.4.0.2`, `BH==1.87.0-1`, `StanHeaders==2.32.10`, `rstan==2.32.7` from `https://cloud.r-project.org/`.
  - Local-only input: `/app/data.csv`.
  - No Git, PyPI, model, dataset, or external API download is required.
- **Evidence**:
  - `instruction.md`: explicitly requires installing RStan 2.32.7 and Stan dependencies; data is local at `/app/data.csv`.
  - `README.md`: internet is allowed and required for CRAN package installation.
  - `solution/solve.sh`: installs `gfortran`, `liblapack-dev`, and `libblas-dev` with APT, then installs `remotes`, `Rcpp`, `RcppEigen`, `BH`, `StanHeaders`, and `rstan` from `https://cloud.r-project.org/`.
- **Routing**: APT cache/proxy plus CRAN/R package cache for pinned RStan dependency versions.
- **Judgment**: High runtime-network and build-risk task; generic Python PyPI cache does not help.

### `merge-diff-arc-agi-task`

- **Runtime need**: APT package installation in the reference solution.
- **Exact runtime downloads**:
  - `apt`: `git`.
  - Local-only inputs: `/app/bundle1.bundle`, `/app/bundle2.bundle`, `/app/examples.json`.
  - Local Git operations: `git fetch /app/bundle*.bundle` are from local bundle files, not external fetches.
  - No PyPI, external Git clone/fetch, model, dataset, or external API download is required.
- **Evidence**:
  - `instruction.md`: both Git bundles and ARC examples are local under `/app`.
  - `README.md`: pre-installed files are `/app/bundle1.bundle`, `/app/bundle2.bundle`, and `/app/examples.json`.
  - `solution/solve.sh`: runs `apt-get update` and `apt-get install -y git`, then initializes a local repo and fetches from local bundle files.
- **Routing**: APT cache/proxy for Git if it is not already present.
- **Judgment**: No external repository or dataset; only package installation may touch network.

### `model-extraction-relu-logits`

- **Runtime need**: None confirmed.
- **Exact runtime downloads**:
  - `none`: reference path writes `/app/steal.py` and queries the local black-box model through `/app/forward.py`.
  - Local/preinstalled resources: `/app/forward.py`, Python, and `numpy==2.2.5`.
  - No APT package, PyPI package, Git repository, model checkpoint, dataset, or external API is required.
- **Evidence**:
  - `instruction.md`: model is queried locally by importing `forward.py`; output is local `/app/stolen_A1.npy`.
  - `README.md`: NumPy is installed in the environment.
  - `solution/solve.sh`: writes `steal.py` that uses local `forward` and NumPy; no package install, clone, download, or external API.
- **Routing**: No special runtime routing.
- **Judgment**: This is local numerical/model-extraction work.

### `modernize-scientific-stack`

- **Runtime need**: None confirmed.
- **Exact runtime downloads**:
  - `none`: reference path only creates `/app/requirements.txt` and `/app/analyze_climate_modern.py`, then runs the local script.
  - Local/preinstalled resources: `/app/climate_analyzer/analyze_climate.py`, `/app/climate_analyzer/sample_data/climate_data.csv`, `/app/climate_analyzer/config.ini`, `numpy==2.3.2`, `pandas==2.3.2`, `matplotlib==3.10.6`, and `scipy==1.16.1`.
  - No APT package, PyPI package, Git repository, model checkpoint, dataset, or external API is required.
- **Evidence**:
  - `instruction.md`: legacy script, CSV, and config are local under `/app/climate_analyzer`.
  - `README.md`: NumPy, pandas, matplotlib, and scipy are pre-installed.
  - `solution/solve.sh`: writes `/app/requirements.txt` and `/app/analyze_climate_modern.py`, then runs the local script; no package install/download.
- **Routing**: No special runtime routing.
- **Judgment**: The dependency file is a deliverable, not evidence that packages must be downloaded.

### `mteb-leaderboard`

- **Runtime need**: External Git clone for benchmark results; optionally web/HF leaderboard access.
- **Exact runtime downloads**:
  - Git repository: `https://github.com/embeddings-benchmark/results`, then `git checkout c0fd3271442f8c0c839084a109fede85c8514dab`.
  - Optional alternate route mentioned by the reference: `http://mteb-leaderboard.hf.space/?benchmark_name=MTEB%28Scandinavian%2C+v1%29`.
  - Optional alternate route mentioned by the reference: MTEB CLI branch/PR `https://github.com/embeddings-benchmark/mteb/pull/2454`.
  - Local/preinstalled resources: `mteb==1.38.41`, Git, GCC/G++, wget, tmux, and asciinema.
  - No model checkpoint or dataset download is required by the reference path beyond the results repository.
- **Evidence**:
  - `instruction.md`: asks for the best model according to Scandinavian MTEB leaderboard as of August 2025.
  - `README.md`: internet access is required for MTEB data; mteb library is pre-installed.
  - `solution/solve.sh`: runs `git clone https://github.com/embeddings-benchmark/results`, checks out commit `c0fd3271442f8c0c839084a109fede85c8514dab`, then analyzes results locally.
- **Routing**: GitHub mirror/cache for `embeddings-benchmark/results` at the required commit. Browser/HF route is secondary and less reproducible.
- **Judgment**: Task-aware Git repository caching is needed for reliable offline or poor-network runs.

### `mteb-retrieve`

- **Runtime need**: HuggingFace/model download through the MTEB library.
- **Exact runtime downloads**:
  - HuggingFace model/tokenizer artifacts: `BAAI/bge-small-zh-v1.5` at revision `7999e1d3359715c523056ef9478215996d62a620`, loaded through `mteb.get_model(...)`.
  - Local/preinstalled resources: `/app/data.txt`, `mteb==1.36.8`, PyTorch as pulled by the task image, tmux, asciinema, GCC, and G++.
  - No APT package, PyPI package, external Git repository, separate dataset, or external API is required by the reference path.
- **Evidence**:
  - `instruction.md`: requires `bge-small-zh-v1.5` at revision `7999e1d3359715c523056ef9478215996d62a620`; `/app/data.txt` is local.
  - `README.md`: internet access is enabled to download the embedding model.
  - `solution/solve.sh`: calls `mteb.get_model("BAAI/bge-small-zh-v1.5", revision="7999e1d3359715c523056ef9478215996d62a620")`.
- **Routing**: HuggingFace cache/prepull for `BAAI/bge-small-zh-v1.5` at the pinned revision.
- **Judgment**: No package install is shown; model artifact cache is the important optimization.

### `multi-source-data-merger`

- **Runtime need**: None confirmed.
- **Exact runtime downloads**:
  - `none`: reference path reads local source files and writes local output files.
  - Local/preinstalled resources: `/data/source_a/users.json`, `/data/source_b/users.csv`, `/data/source_c/users.parquet`, `pandas==2.2.3`, and `pyarrow==17.0.0`.
  - No APT package, PyPI package, Git repository, model checkpoint, dataset, or external API is required by the agent runtime path.
- **Evidence**:
  - `instruction.md`: all input sources are local under `/data/source_a`, `/data/source_b`, and `/data/source_c`.
  - `README.md`: pandas and pyarrow are pre-installed.
  - `solution/solve.sh`: runs inline Python using `json` and `pandas`; no package install, clone, download, or external API.
- **Routing**: No special runtime routing.
- **Judgment**: Local ETL only.

### `nginx-request-logging`

- **Runtime need**: APT package installation.
- **Exact runtime downloads**:
  - APT package index: `apt-get update`.
  - APT package: `nginx` via `apt-get install -y nginx`.
  - Local/preinstalled resources: Python 3.13, curl, and `requests` are present in the image; generated files are local under `/etc/nginx`, `/var/www/html`, and `/var/log/nginx`.
  - Runtime HTTP checks use `http://localhost:8080`; no external web content, PyPI package, Git repository, model checkpoint, dataset, or external API is required.
- **Evidence**:
  - `instruction.md`: first requirement is to install Nginx.
  - `README.md`: base image does not list Nginx as pre-installed; internet is enabled.
  - `solution/solve.sh`: runs `apt-get update && apt-get install -y nginx`, then writes local Nginx config and HTML files.
- **Routing**: APT cache/proxy for Nginx.
- **Judgment**: No external content is needed; package installation is the network-sensitive step.

### `openssl-selfsigned-cert`

- **Runtime need**: None confirmed.
- **Exact runtime downloads**:
  - `none`: reference path uses preinstalled OpenSSL and Python to generate local certificate artifacts.
  - Local/preinstalled resources: OpenSSL, Python 3.13, generated `/app/ssl/server.key`, `/app/ssl/server.crt`, `/app/ssl/server.pem`, `/app/ssl/verification.txt`, and `/app/check_cert.py`.
  - No APT package, PyPI package, Git repository, model checkpoint, dataset, or external API is required during agent runtime.
- **Evidence**:
  - `instruction.md`: asks to generate local key/certificate files under `/app/ssl`.
  - `README.md`: OpenSSL and Python are pre-installed.
  - `solution/solve.sh`: uses local `openssl`, shell file operations, and Python standard libraries; no install/download.
- **Routing**: No special runtime routing.
- **Judgment**: Pure local certificate generation.

### `overfull-hbox`

- **Runtime need**: APT package installation in the reference solution.
- **Exact runtime downloads**:
  - APT package index: `apt update`.
  - APT package in reference path: `python3-pip` via `apt install -y python3-pip`.
  - Local/preinstalled resources: `texlive-latex-base==2023.20240207-1`, `pdflatex`, `/app/main.tex`, `/app/input.tex`, and `/app/synonyms.txt`.
  - No PyPI package, Git repository, model checkpoint, dataset, or external API is required; the `python3-pip` install is not semantically necessary for the shown Python-stdlib solution.
- **Evidence**:
  - `instruction.md`: all files are local (`main.tex`, `input.tex`, `synonyms.txt`) and TeX distribution is installed.
  - `README.md`: TeX distribution is present.
  - `solution/solve.sh`: runs `apt update` and `apt install -y python3-pip`, then uses local Python and `pdflatex`.
- **Routing**: APT cache/proxy if agents install Python tooling.
- **Judgment**: No task-specific external data; package install is likely avoidable but should be covered generically.

### `password-recovery`

- **Runtime need**: None confirmed.
- **Exact runtime downloads**:
  - `none`: reference path searches local disk-image bytes and writes `/app/recovered_passwords.txt`.
  - Local/preinstalled resources: `/app/varsea/disks/**/*.dat`, shell utilities (`find`, `grep`, `dd`, `tr`, `awk`, `sort`), and forensic tools already in the image (`extundelete`, `foremost`, `sleuthkit`, `e2fsprogs`, `binutils`, `zip/unzip`).
  - No APT package, PyPI package, Git repository, model checkpoint, dataset, or external API is required during agent runtime.
- **Evidence**:
  - `instruction.md`: deleted file/data is somewhere under local `/app`.
  - `README.md`: forensic tools are already available in the environment; disk image is local under `/app/varsea/disks/`.
  - `solution/solve.sh`: finds the local disk image and uses `grep`, `dd`, `tr`, `awk`, and shell loops; no package install/download.
- **Routing**: No special runtime routing.
- **Judgment**: Local forensic recovery only.

### `path-tracing`

- **Runtime need**: None confirmed.
- **Exact runtime downloads**:
  - `none`: reference path writes a standalone local `image.c` renderer and does not install or fetch anything.
  - Local/preinstalled resources: `/app/image.ppm`, GCC, curl, ffmpeg, system C headers, and math library `-lm`.
  - No APT package, PyPI package, Git repository, model checkpoint, dataset, image asset download, or external API is required during agent runtime.
- **Evidence**:
  - `instruction.md`: reference image is local; output is generated by local `image.c`.
  - `README.md`: gcc is pre-installed.
  - `solution/solve.sh`: writes a standalone C program; no package install, clone, download, or external API.
- **Routing**: No special runtime routing.
- **Judgment**: Local reverse-engineering/rendering only.

### `path-tracing-reverse`

- **Runtime need**: None confirmed.
- **Exact runtime downloads**:
  - `none`: reference path writes standalone `/app/mystery.c` and does not install or fetch anything.
  - Local/preinstalled resources: `/app/mystery`, GCC, G++, curl, `zlib1g-dev`, `libjpeg-dev`, system C headers, and math library `-lm`.
  - No APT package, PyPI package, Git repository, model checkpoint, dataset, image asset download, or external API is required during agent runtime.
- **Evidence**:
  - `instruction.md`: compiled binary `/app/mystery` is local; output source is local `/app/mystery.c`.
  - `README.md`: gcc and related tools are available.
  - `solution/solve.sh`: writes a standalone C program; no package install, clone, download, or external API.
- **Routing**: No special runtime routing.
- **Judgment**: Local binary reverse engineering and rendering reconstruction.

### `polyglot-c-py`

- **Runtime need**: None confirmed.
- **Exact runtime downloads**:
  - `none`: reference path only creates `/app/polyglot/main.py.c`.
  - Local/preinstalled resources: Python `3.12.3`, GCC `13.2.0`, system C headers, and shell utilities.
  - No APT package, PyPI package, Git repository, model checkpoint, dataset, or external API is required during agent runtime.
- **Evidence**:
  - `instruction.md`: asks to create a single local `/app/polyglot/main.py.c`.
  - `README.md`: gcc and Python are provided in the environment.
  - `solution/solve.sh`: writes the polyglot file; no package install, clone, download, or external API.
- **Routing**: No special runtime routing.
- **Judgment**: Local programming task only.

### `polyglot-rust-c`

- **Runtime need**: None confirmed.
- **Exact runtime downloads**:
  - `none`: reference path only creates `/app/polyglot/main.rs`.
  - Local/preinstalled resources: `rustc==1.75.0`, `g++==13.2.0`, system C++ headers, and shell utilities.
  - No APT package, Cargo crate, PyPI package, Git repository, model checkpoint, dataset, or external API is required during agent runtime.
- **Evidence**:
  - `instruction.md`: asks to create one local `/app/polyglot/main.rs`.
  - `README.md`: `rustc` and `g++` are installed.
  - `solution/solve.sh`: writes the polyglot source file; no package install, clone, download, or external API.
- **Routing**: No special runtime routing.
- **Judgment**: Local programming task only.

### `portfolio-optimization`

- **Runtime need**: None confirmed.
- **Exact runtime downloads**:
  - `none`: reference path edits local skeletons and runs `python3 setup.py build_ext --inplace`.
  - Local/preinstalled resources: `/app/portfolio_baseline.py`, `/app/portfolio_optimized.c`, `/app/portfolio_optimized.py`, `/app/setup.py`, `/app/benchmark.py`, build tools (`gcc`, `g++`, `build-essential`), `numpy==2.3.2`, and `setuptools==78.1.1`.
  - No APT package, PyPI package, Git repository, model checkpoint, dataset, or external API is required during agent runtime.
- **Evidence**:
  - `instruction.md`: asks to complete local skeleton files and build locally with `setup.py`.
  - `README.md`: build tools, NumPy, and setuptools are pre-installed.
  - `solution/solve.sh`: writes local C and Python files, then runs `python3 setup.py build_ext --inplace`; no package install, clone, download, or API call.
- **Routing**: No special runtime routing.
- **Judgment**: Local compilation/performance optimization only.

### `protein-assembly`

- **Runtime need**: PyPI package installation plus external bioinformatics API/resource access.
- **Exact runtime downloads**:
  - PyPI packages: `biopython==1.85`, `rcsb-api==1.4.0`, `dnachisel==3.2.16`, `pandas==2.2.3`, and `requests==2.32.4`.
  - RCSB API through `rcsb-api`: sequence-similarity search for each antibody FASTA record, plus `DataQuery(..., return_data_list=["struct.title"])`.
  - PubChem PUG REST: `https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/smiles/{encoded_smiles}/cids/TXT` and `https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/synonyms/TXT`.
  - RCSB FASTA: `https://www.rcsb.org/fasta/entry/{pdb_id}/download` for each PDB ID in `/app/pdb_ids.txt`.
  - RCSB entry metadata: `https://data.rcsb.org/rest/v1/core/entry/{pdb_id}` for each usable PDB ID.
  - FPbase protein search: `https://www.fpbase.org/api/proteins/?pdb__contains={pdb_id}&format=json` for each usable PDB ID.
  - Local resources: `/app/antibody.fasta`, `/app/plasmid.gb`, and `/app/pdb_ids.txt`.
  - No APT package, Git repository, model checkpoint, or separate dataset download is required by the reference path.
- **Evidence**:
  - `instruction.md`: local files include `antibody.fasta`, `plasmid.gb`, and `pdb_ids.txt`, but the task requires protein identities and spectral data that must match external API data.
  - `README.md`: explicitly says the agent must query PDB, FPBase, and PubChem REST APIs; internet access is required.
  - `solution/solve.sh`: installs `biopython`, `rcsb-api`, `dnachisel`, `pandas`, and `requests`; uses RCSB sequence/search/data APIs, PubChem PUG REST, `https://www.rcsb.org/fasta/entry/.../download`, `https://data.rcsb.org/rest/v1/core/entry/...`, and `https://www.fpbase.org/api/proteins/...`.
- **Routing**: Wheelhouse/PyPI mirror plus task-aware API/cache layer for RCSB, PubChem, and FPBase responses for the fixed input IDs/SMILES.
- **Judgment**: High runtime-network risk. This is not solved by a generic PyPI cache; external scientific API access is part of the intended solution.

### `prove-plus-comm`

- **Runtime need**: None confirmed.
- **Exact runtime downloads**:
  - `none`: reference path overwrites/completes local `plus_comm.v` and runs `coqc plus_comm.v`.
  - Local/preinstalled resources: Coq proof assistant, `/workspace/plus_comm.v`, and Coq standard library module `Arith`.
  - No APT package, OPAM package, PyPI package, Git repository, model checkpoint, dataset, or external API is required during agent runtime.
- **Evidence**:
  - `instruction.md`: local `plus_comm.v` must be fixed and compiled.
  - `README.md`: Coq proof assistant is installed.
  - `solution/solve.sh`: writes `plus_comm.v` and runs local `coqc`; no package install/download.
- **Routing**: No special runtime routing.
- **Judgment**: Local theorem proving only.

### `pypi-server`

- **Runtime need**: APT package installation plus PyPI package installation.
- **Exact runtime downloads**:
  - APT package index: `apt-get update`.
  - APT packages: `python3`, `python3-pip`, `python3-venv`, and `apache2-utils`.
  - PyPI packages into `/opt/pypiserver/venv`: `pypiserver==2.3.2`, `passlib[bcrypt]==1.7.4`, `bcrypt==4.3.0`, and `packaging==25.0`.
  - PyPI packages into system/default Python in the reference path: `build==1.3.0` and `twine==6.1.0`; the script installs these once with `python3 -m pip` and again with `pip`.
  - Local server route: `http://localhost:8080` and `http://localhost:8080/simple` for upload/install of the locally built `vectorops==0.1.0` package.
  - Local/generated resources: `/opt/pypiserver/.htpasswd`, `/srv/pypi-packages`, `/tmp/vectorops`, and `/var/log/pypiserver.log`.
  - No external Git repository, model checkpoint, dataset, or external API is required.
- **Evidence**:
  - `instruction.md`: package and PyPI server are local, but tooling must be installed.
  - `README.md`: internet is enabled to install dependencies.
  - `solution/solve.sh`: installs `python3`, `python3-pip`, `python3-venv`, and `apache2-utils` with APT; installs `pypiserver`, `passlib[bcrypt]`, `bcrypt`, `packaging`, `build`, and `twine` with pip; then creates and serves a local package.
- **Routing**: APT cache/proxy plus PyPI mirror for server/build/upload tooling.
- **Judgment**: Runtime network risk is dependency installation, not external package data.

### `pytorch-model-cli`

- **Runtime need**: PyPI/PyTorch wheel installation plus APT package installation.
- **Exact runtime downloads**:
  - PyTorch CPU wheel index: `https://download.pytorch.org/whl/cpu`.
  - PyPI/PyTorch package: `torch==2.7.0` via `pip install torch==2.7.0 --index-url https://download.pytorch.org/whl/cpu`.
  - PyPI package: `numpy` via `pip install numpy` with no version pin in the reference path.
  - APT package index: `apt-get update`.
  - APT package: `build-essential`.
  - Local/preinstalled resources: `/app/model.pth`, `/app/model.py`, `/app/image.png`, `/app/cJSON.c`, `/app/cJSON.h`, `/app/lodepng.cpp`, and `/app/lodepng.h`.
  - No external Git repository, model checkpoint, dataset, or external API is required by the agent runtime path; verifier-side MNIST download is outside this inventory.
- **Evidence**:
  - `instruction.md`: model weights, image, and required outputs are local.
  - `README.md`: dependencies such as `model.pth`, `model.py`, `cJSON`, and `lodepng` are provided.
  - `solution/solve.sh`: installs `torch==2.7.0` from `https://download.pytorch.org/whl/cpu`, installs `numpy`, runs `apt-get update`, and installs `build-essential`.
- **Routing**: Wheelhouse/PyPI/PyTorch CPU wheel cache for `torch==2.7.0` and NumPy, plus APT cache/proxy for build tools.
- **Judgment**: Runtime network risk is dependency installation; model artifacts are local.

### `pytorch-model-recovery`

- **Runtime need**: None confirmed.
- **Exact runtime downloads**:
  - `none`: reference path reads local tensors, writes local Python scripts, fine-tunes locally, and saves `/app/model.pt`.
  - Local/preinstalled resources: `/app/weights.pt`, `/app/dataset.pt`, `torch==2.7.1`, and `nano`.
  - No APT package, PyPI package, PyTorch wheel download, Git repository, model checkpoint, dataset, or external API is required during agent runtime.
- **Evidence**:
  - `instruction.md`: weights and dataset are local at `/app/weights.pt` and `/app/dataset.pt`.
  - `README.md`: `torch==2.7.1` is pre-installed.
  - `solution/solve.sh`: writes local inspection/model scripts and trains with local PyTorch; no package install/download.
- **Routing**: No special runtime routing.
- **Judgment**: Local model reconstruction/fine-tuning only.

### `qemu-alpine-ssh`

- **Runtime need**: Alpine package installation inside the VM via `apk`.
- **Exact runtime downloads**:
  - Guest VM package index: Alpine `apk update` from the guest network.
  - Guest VM package: `openssh` via `apk add openssh`.
  - Guest VM DHCP/network setup: `udhcpc -i eth0`, using QEMU user networking with `hostfwd=tcp::2222-:22`.
  - Host local/preinstalled resources: `/app/alpine.iso`, `/app/alpine-disk.qcow2`, `qemu-system-x86_64`, telnet, expect, tmux, netcat, wget, and openssh-client.
  - No host-side APT package, PyPI package, Git repository, model checkpoint, dataset, or external API is required during agent runtime.
- **Evidence**:
  - `instruction.md`: Alpine ISO is local at `/app/alpine.iso`.
  - `README.md`: QEMU and helper tools are installed; internet is required for package installation.
  - `solution/solve.sh`: boots local ISO/disk with QEMU, then inside Alpine runs `apk update` and `apk add openssh`.
- **Routing**: Alpine APK mirror/cache or task-aware preconfigured VM image with OpenSSH already available.
- **Judgment**: Host package cache is not enough; the agent runtime dependency happens inside the guest VM.

### `qemu-startup`

- **Runtime need**: None confirmed.
- **Exact runtime downloads**:
  - `none`: reference path starts local QEMU resources and waits for the local telnet serial console.
  - Local/preinstalled resources: `/app/alpine.iso`, `/app/alpine-disk.qcow2`, `qemu-system-x86_64`, qemu-utils, telnet, expect, netcat, tmux, and asciinema.
  - No guest `apk` package, host APT package, PyPI package, Git repository, model checkpoint, dataset, or external API is required during agent runtime.
- **Evidence**:
  - `instruction.md`: Alpine ISO is local at `/app/alpine.iso`; task only requires booting and exposing serial console via telnet.
  - `README.md`: QEMU, telnet, expect, and netcat are pre-installed.
  - `solution/solve.sh`: starts QEMU using local ISO/disk and waits for telnet login prompt; no package install/download inside or outside the VM.
- **Routing**: No special runtime routing.
- **Judgment**: Pure local VM startup.

### `query-optimize`

- **Runtime need**: None confirmed.
- **Exact runtime downloads**:
  - `none`: reference path writes a single local SQL query to `/app/sol.sql`.
  - Local/preinstalled resources: `/app/oewn.sqlite`, `/app/my-sql-query.sql`, SQLite/sqlite3, and curl.
  - No APT package, PyPI package, Git repository, model checkpoint, dataset, or external API is required during agent runtime; the OEWN database is already present in the task image.
- **Evidence**:
  - `instruction.md`: SQLite database and original query are local under `/app`.
  - `README.md`: `sqlite3` and `curl` are available; database is local/read-only.
  - `solution/solve.sh`: writes `/app/sol.sql`; no package install, clone, download, or external API.
- **Routing**: No special runtime routing.
- **Judgment**: Local SQL rewrite only.

### `raman-fitting`

- **Runtime need**: PyPI package installation.
- **Exact runtime downloads**:
  - PyPI packages: `scipy==1.16.0` and `numpy==2.3.1`.
  - Local resources: `/app/graphene.dat`; generated local files include `/app/graphene_format.dat`, `/app/convert.py`, `/app/fit.py`, and `/app/results.json`.
  - No APT package, Git repository, model checkpoint, dataset download, or external API is required.
- **Evidence**:
  - `instruction.md`: Raman data file is local.
  - `README.md`: scipy and numpy must be installed by the agent.
  - `solution/solve.sh`: runs `pip install scipy==1.16.0 numpy==2.3.1`, then fits local `graphene.dat`.
- **Routing**: Wheelhouse/PyPI mirror for SciPy and NumPy.
- **Judgment**: No external spectroscopy data; dependency installation is the runtime network risk.

### `regex-chess`

- **Runtime need**: None confirmed.
- **Exact runtime downloads**:
  - `none`: reference path decodes an embedded base64 gzip payload into local `/app/re.json`.
  - Local/preinstalled resources: Python stdlib `base64`, gzip CLI, and `python-chess`/`chess` already present for local checking or verification.
  - No APT package, PyPI package, Git repository, model checkpoint, dataset, or external API is required during agent runtime.
- **Evidence**:
  - `instruction.md`: asks only to create local `/app/re.json`.
  - `README.md`: python-chess is installed for verification.
  - `solution/solve.sh`: embeds a base64 gzip payload and decodes it locally into `re.json`; no package install, clone, download, or external API.
- **Routing**: No special runtime routing.
- **Judgment**: Local artifact generation only.

### `regex-log`

- **Runtime need**: None confirmed.
- **Exact runtime downloads**:
  - `none`: reference path writes the regex directly to local `/app/regex.txt`.
  - Local/preinstalled resources: Python `re` is used by verification; no task data file is fetched by the agent.
  - No APT package, PyPI package, Git repository, model checkpoint, dataset, or external API is required during agent runtime.
- **Evidence**:
  - `instruction.md`: asks only to save a regex into `/app/regex.txt`.
  - `README.md`: verification reads that local regex file with Python `re.findall`.
  - `solution/solve.sh`: writes `/app/regex.txt` directly with `cat`; no package install, clone, download, or external API.
- **Routing**: No special runtime routing.
- **Judgment**: Local regex construction only.

### `reshard-c4-data`

- **Runtime need**: None required for the task itself; optional PyPI only if the agent chooses non-stdlib dependencies.
- **Exact runtime downloads**:
  - Required by the task semantics: `none`, if `compress.py` and `decompress.py` are implemented with the Python standard library and `pyproject.toml` has no dependencies.
  - Reference-solution caveat: the shown scripts import `tqdm` but do not create `pyproject.toml`; a faithful dependency-managed version of that path would need `tqdm` in `/app/pyproject.toml` and would fetch it during `uv sync` unless already cached.
  - Local/preinstalled resources: `/app/c4_sample/`, uv/uvx, Python stdlib modules (`glob`, `gzip`, `hashlib`, `json`, `os`, `sys`), and generated `/app/compress.py`, `/app/decompress.py`, `/app/pyproject.toml`.
  - Verifier-side data generation may fetch `allenai/c4` shard `en/c4-train.00009-of-01024.json.gz`; that is outside the agent runtime path.
  - No APT package, Git repository, model checkpoint, external API, or agent-side dataset download is required.
- **Evidence**:
  - `instruction.md`: sample C4 data is provided locally in `/app/c4_sample/`; required outputs are `/app/compress.py`, `/app/decompress.py`, and `/app/pyproject.toml`.
  - `README.md`: `uv` is pre-installed; tests run `uv sync`, but the scripts can be implemented with the Python standard library and an empty dependency set.
  - `solution/solve.sh`: writes local Python scripts. It imports `tqdm`, but the task semantics do not require downloading external datasets or models.
- **Routing**: Prefer stdlib/no-dependency implementation. If an agent adds packages, route through PyPI mirror via `uv sync`.
- **Judgment**: Local file transformation task. Runtime network risk is avoidable and comes from agent dependency choices, not task data.

### `rstan-to-pystan`

- **Runtime need**: APT/PPA packages, GitHub source clone, and PyPI package installation.
- **Exact runtime downloads**:
  - APT package index: `apt-get update -qq` before base build tools and again around the Python PPA setup.
  - APT packages: `build-essential`, `gfortran`, `libatlas-base-dev`, `liblapack-dev`, `libblas-dev`, `curl`, `ca-certificates`, `gnupg`, `software-properties-common`, `git`, `make`, `cmake`, and `pkg-config`.
  - APT/PPA source: `ppa:deadsnakes/ppa` via `add-apt-repository -y ppa:deadsnakes/ppa`.
  - APT packages from/after that PPA: `python3.10`, `python3.10-venv`, `python3.10-dev`, and `python3.10-distutils`.
  - PyPI bootstrap packages in `/opt/py310`: upgraded `pip`, `setuptools`, and `wheel`.
  - Git repository: `https://github.com/stan-dev/httpstan.git`, cloned with `--depth 1 --branch 4.13.0` to `/tmp/httpstan`.
  - Local source build: `make -C /tmp/httpstan -j$(nproc)` and `python -m poetry -C /tmp/httpstan build -v`.
  - PyPI package for build tooling: `poetry~=1.8`.
  - Local wheel install: `/tmp/httpstan/dist/*.whl`.
  - PyPI packages after httpstan build: `pystan==3.10.0`, `pandas==2.2.2`, `numpy==1.26.4`, and `nest_asyncio==1.6.0`.
  - Local resources: `/app/train_X.csv`, `/app/train_y.csv`, `/app/test_X.csv`, `/app/meta_public.json`, and `/app/gp_rstan.R`.
  - No external dataset, model checkpoint, or external API is required beyond package/source retrieval.
- **Evidence**:
  - `instruction.md`: explicitly requires installing PyStan 3.10.0 and forbids R/RStan/cmdstan alternatives.
  - `README.md`: internet is required for package installation; key dependencies include PyStan 3.10.0, httpstan 4.13.0, pandas, and numpy.
  - `solution/solve.sh`: runs `apt-get`, adds `ppa:deadsnakes/ppa`, installs Python 3.10 build tooling, clones `https://github.com/stan-dev/httpstan.git`, builds it, then installs `pystan`, `pandas`, `numpy`, and `nest_asyncio` from PyPI.
- **Routing**: Needs APT/PPA mirror coverage, GitHub mirror/cache for `stan-dev/httpstan`, and PyPI mirror for PyStan/httpstan dependencies.
- **Judgment**: High runtime-network risk. This task is package/build dominated before the modeling code can run.

### `sam-cell-seg`

- **Runtime need**: APT packages, PyPI packages, PyTorch CPU wheel index, and GitHub install of MobileSAM.
- **Exact runtime downloads**:
  - APT package index: `apt-get update`.
  - APT packages: `git`, `curl`, and `libgl1`.
  - PyTorch CPU wheel index: `https://download.pytorch.org/whl/cpu` through `--extra-index-url`.
  - PyPI/PyTorch packages: `torch==2.5.1+cpu` and `torchvision==0.20.1+cpu`.
  - PyPI packages: `pandas==2.3.2`, `timm==1.0.19`, and `opencv-python==4.12.0.88`.
  - Git/PyPI direct install: `git+https://github.com/ChaoningZhang/MobileSAM.git@34bbbfdface3c18e5221aa7de6032d7220c6c6a1`.
  - Local resources: `/app/demo_rgb.png`, `/app/demo_metadata.csv`, and generated `/app/convert_masks.py`.
  - Runtime argument resource: `weights_path` points to a MobileSAM checkpoint such as `/app/mobile_sam.pt`; the verifier downloads that weight file, so it is outside the agent runtime inventory.
  - No separate dataset download or external API is required by the agent runtime path.
- **Evidence**:
  - `instruction.md`: requires using MobileSAM from `https://github.com/ChaoningZhang/MobileSAM` and writing a CPU-capable `/app/convert_masks.py`.
  - `README.md`: says the agent must install MobileSAM from the official GitHub repository; allowed runtime packages include `torch`, `torchvision`, `opencv-python`, `Pillow`, `tqdm`, and `mobile_sam`.
  - `solution/solve.sh`: installs `git`, `curl`, and `libgl1`; then installs CPU `torch`/`torchvision`, `pandas`, `timm`, `opencv-python`, and `git+https://github.com/ChaoningZhang/MobileSAM.git@34bbbfdface3c18e5221aa7de6032d7220c6c6a1`.
- **Routing**: Needs APT mirror, PyTorch CPU wheel mirror/cache, PyPI cache, and GitHub mirror/cache for MobileSAM. The model weights are passed via `weights_path`; verifier-side weight download is outside the agent runtime inventory.
- **Judgment**: High runtime-network risk from dependency installation and GitHub package install, even though demo image/CSV are local.

### `sanitize-git-repo`

- **Runtime need**: None confirmed inside the agent attempt.
- **Exact runtime downloads**:
  - `none`: reference path edits the already-cloned local `/app/dclm` repository using local text tools.
  - Local/preinstalled resources: `/app/dclm`, Git metadata, `grep`, `find`, and `sed`.
  - Contaminated local files: `/app/dclm/ray_processing/ray_cluster.yaml`, `/app/dclm/ray_processing/process.py`, and `/app/dclm/exp_data/datasets/tokenized/rw_v2_fasttext_openhermes_vs_rw_v2_bigram_0.1_arcade100k.json`.
  - Secret replacement targets: AWS access key regex `AKIA[0-9A-Z]{16}` -> `<your-aws-access-key-id>`; known AWS secret value, case-insensitive -> `<your-aws-secret-access-key>`; GitHub token regex `gh[pousr]_[A-Za-z0-9]{20,}` -> `<your-github-token>`; HuggingFace token regex `hf_[A-Za-z0-9]{29,}` -> `<your-huggingface-token>`.
  - No APT package, PyPI package, Git clone, model checkpoint, dataset, or external API is required during the agent attempt. The GitHub clone of `jeffreywpli/test-secret-removal` belongs to environment construction.
- **Evidence**:
  - `instruction.md`: asks to sanitize the existing `dclm` repository by replacing secrets with placeholders.
  - `README.md`: describes `/app/dclm` as the working directory cloned during environment setup from `jeffreywpli/test-secret-removal`.
  - `solution/solve.sh`: uses local `grep`, `find`, and `sed` over `/app/dclm`; no package install, clone, download, or external API.
- **Routing**: No special runtime routing. The GitHub clone belongs to environment construction, not the agent's attempt-time work.
- **Judgment**: Local repository-editing task during runtime.

### `schemelike-metacircular-eval`

- **Runtime need**: None confirmed.
- **Exact runtime downloads**:
  - `none`: the agent writes `/app/eval.scm` and runs the provided local interpreter/tests.
  - Local/preinstalled resources: `/app/interp.py`, visible `/app/test/*.scm` programs, Python 3, and Scheme-like primitives implemented by `interp.py`.
  - Required output: local `/app/eval.scm`.
  - No APT package, PyPI package, Git clone, model checkpoint, dataset, or external API is required during the agent attempt. Verifier-side `uvx`/pytest setup is outside scope.
- **Evidence**:
  - `instruction.md`: asks to write `eval.scm` for the provided Scheme-like interpreter.
  - `README.md`: `interp.py` and the visible `test/` programs are provided in `/app`.
  - `solution/solve.sh`: writes `eval.scm` directly; no package install, clone, download, or external API.
- **Routing**: No special runtime routing.
- **Judgment**: Local interpreter/programming task.

### `sparql-university`

- **Runtime need**: None confirmed.
- **Exact runtime downloads**:
  - `none`: the agent writes a SPARQL query to `/app/solution.sparql`.
  - Local resources: `/app/university_graph.ttl` and the RDF ontology/data embedded in that Turtle file.
  - Query constants: EU ISO 3166-1 alpha-2 country codes as of `2025-08-16`, encoded directly in the query via `VALUES`.
  - No APT package, PyPI package, Git clone, model checkpoint, dataset, or external API is required during the agent attempt. Verifier-side `rdflib` installation is outside scope.
- **Evidence**:
  - `instruction.md`: RDF/Turtle graph is provided locally at `/app/university_graph.ttl`; output is a local `/app/solution.sparql`.
  - `README.md`: verifier uses rdflib, but the agent's task is only to write the query.
  - `solution/solve.sh`: writes `/app/solution.sparql` directly; no package install, clone, download, or external API.
- **Routing**: No special runtime routing.
- **Judgment**: Local query-writing task. EU country membership can be encoded directly; no runtime data download is required.

### `sqlite-db-truncate`

- **Runtime need**: None confirmed.
- **Exact runtime downloads**:
  - `none`: the agent recovers data from the local corrupted SQLite file and writes local JSON output.
  - Local/preinstalled resources: `/app/trunc.db`, Python 3, and standard-library modules `struct`, `binascii`, and `json`.
  - Required output: `/app/recover.json`.
  - No APT package, PyPI package, Git clone, model checkpoint, dataset, or external API is required during the agent attempt. Verifier-side `uvx`/pytest setup is outside scope.
- **Evidence**:
  - `instruction.md`: corrupted database is provided locally at `/app/trunc.db`; output is `/app/recover.json`.
  - `README.md`: task is binary recovery of the provided local SQLite file.
  - `solution/solve.sh`: writes and runs a local Python script using only standard-library modules (`struct`, `binascii`, `json`); no package install, clone, download, or external API.
- **Routing**: No special runtime routing.
- **Judgment**: Local binary-analysis/data-recovery task.

### `sqlite-with-gcov`

- **Runtime need**: APT package installation.
- **Exact runtime downloads**:
  - APT index: `apt-get update`.
  - APT packages: `fossil`, `tzdata`, `gcc`, `jimsh`, `tclsh`, and `make`.
  - Local source archive: `/app/vendor/sqlite-fossil-release.tar.gz`; the task explicitly says to use this pre-vendored snapshot instead of fetching SQLite source over the network.
  - Local build outputs: `/app/sqlite/sqlite3`, gcov `.gcno`/`.gcda` files under `/app/sqlite`, and symlink `/usr/local/bin/sqlite3`.
  - No Git clone, SQLite source download, PyPI package, model checkpoint, dataset, or external API is required during the agent attempt.
- **Evidence**:
  - `instruction.md`: SQLite source snapshot is pre-vendored at `/app/vendor/sqlite-fossil-release.tar.gz` and should be used instead of fetching sources over the network.
  - `README.md`: task requires compiling SQLite with gcov instrumentation; source is local.
  - `solution/solve.sh`: installs `fossil`, `tzdata`, `gcc`, `jimsh`, `tclsh`, and `make` via `apt-get`, then builds from the local tarball.
- **Routing**: APT mirror/cache for build tools. No Git/source download routing needed for SQLite itself.
- **Judgment**: Runtime network risk is package installation only; source acquisition is local by task design.

### `torch-pipeline-parallelism`

- **Runtime need**: None confirmed inside the agent attempt.
- **Exact runtime downloads**:
  - `none`: the agent writes `/app/pipeline_parallel.py`.
  - Local/preinstalled runtime interfaces: `torch`, `torch.distributed`, `torch.nn`, and `torch.nn.functional`.
  - Test-supplied objects: an already-constructed `LlamaForCausalLM` instance, input microbatches, target microbatches, initialized process group, `device`, and `dtype`.
  - No HuggingFace model/tokenizer download is required: tests construct the model from `LlamaConfig` rather than calling `from_pretrained`.
  - No APT package, PyPI package, Git clone, model checkpoint, dataset, or external API is required during the agent attempt. Verifier-side `torch==2.7.0` and `transformers==4.55.0` installation is outside scope.
- **Evidence**:
  - `instruction.md`: asks to create `/app/pipeline_parallel.py`; process group and model are supplied by tests.
  - `README.md`: PyTorch, Transformers, and pytest are environment dependencies for the task image/test harness.
  - `solution/solve.sh`: writes `pipeline_parallel.py` directly; no package install, model download, clone, or external API.
- **Routing**: No special runtime routing.
- **Judgment**: Local distributed-training implementation task. Runtime failures should be code correctness, not network availability.

### `torch-tensor-parallelism`

- **Runtime need**: None confirmed inside the agent attempt.
- **Exact runtime downloads**:
  - `none`: the agent writes `/app/parallel_linear.py`.
  - Local/preinstalled runtime interfaces: `torch`, `torch.nn.functional`, and `torch.distributed`.
  - Test-supplied data: initialized distributed process group, `master_weight` tensors, inputs, and expected sharding dimensions.
  - No model checkpoint, pretrained model/tokenizer, dataset, APT package, PyPI package, Git clone, or external API is required during the agent attempt. Verifier-side `torch==2.7.0` installation is outside scope.
- **Evidence**:
  - `instruction.md`: asks to create `/app/parallel_linear.py`; distributed ranks and weights are supplied by tests.
  - `README.md`: PyTorch and pytest are environment dependencies.
  - `solution/solve.sh`: writes `parallel_linear.py` directly; no package install, model download, clone, or external API.
- **Routing**: No special runtime routing.
- **Judgment**: Local PyTorch distributed-layer implementation task.

### `train-fasttext`

- **Runtime need**: APT package installation and GitHub source clone.
- **Exact runtime downloads**:
  - APT index: `apt-get update`.
  - APT packages: `git` and `build-essential`.
  - Git clone: `https://github.com/facebookresearch/fastText.git`.
  - Local/preinstalled resources: `/app/data/train-00000-of-00001.parquet`, `pandas`, `pyarrow`, and `wget`.
  - Local generated files: `/app/data/train.txt`, `/app/convert_data.py`, `/app/fastText/fasttext`, and `/app/model.bin`.
  - No runtime Yelp/HuggingFace dataset download is required by the agent attempt; those parquet files are pulled during environment construction.
- **Evidence**:
  - `instruction.md`: Yelp data is provided locally in `/app/data`; output is `/app/model.bin`.
  - `README.md`: fastText must be installed/compiled, and internet is needed to clone the FastText repository.
  - `solution/solve.sh`: installs `git` and `build-essential`, clones `https://github.com/facebookresearch/fastText.git`, builds it with `make`, converts local parquet data, and trains locally.
- **Routing**: APT mirror/cache for build tools and GitHub mirror/cache for `facebookresearch/fastText`.
- **Judgment**: Runtime network risk is tool acquisition/build setup, not dataset access.

### `tune-mjcf`

- **Runtime need**: None confirmed inside the agent attempt.
- **Exact runtime downloads**:
  - `none`: the agent copies and edits the local MJCF XML file.
  - Local resources: `/app/model_ref.xml` and `/app/eval.py`.
  - Local/preinstalled runtime dependency: MuJoCo `3.3.5` is installed in the task image.
  - Required output: `/app/model.xml`.
  - No MuJoCo plugin search/download is needed; `instruction.md` explicitly says there is no need to look for plugins. No APT package, PyPI package, Git clone, model checkpoint, dataset, or external API is required during the agent attempt.
- **Evidence**:
  - `instruction.md`: reference model `/app/model_ref.xml` and helper `/app/eval.py` are provided locally; output is `/app/model.xml`.
  - `README.md`: MuJoCo is an environment dependency.
  - `solution/solve.sh`: copies the local XML and edits it with `sed`; no package install, clone, download, or external API.
- **Routing**: No special runtime routing.
- **Judgment**: Local XML/simulation-parameter tuning task.

### `video-processing`

- **Runtime need**: None confirmed inside the agent attempt.
- **Exact runtime downloads**:
  - `none`: the agent writes `/app/jump_analyzer.py` and analyzes local MP4 inputs.
  - Local resources: `/app/example_video.mp4`; hidden verifier video is mounted under `/tests/test_video.mp4`.
  - Local/preinstalled allowed libraries: `cv2` from `opencv-contrib-python>=4.11.0.86`, `numpy`, and `toml`; standard-library imports such as `os` and `sys` are allowed.
  - Required output at runtime: `/app/output.toml`.
  - No ffmpeg, YouTube/video download, model checkpoint, dataset, APT package, PyPI package, Git clone, or external API is required during the agent attempt.
- **Evidence**:
  - `instruction.md`: example video is provided locally at `/app/example_video.mp4`; allowed libraries are `toml`, `cv2`, and `numpy`.
  - `README.md`: OpenCV and required system dependencies are installed in the environment.
  - `solution/solve.sh`: writes `/app/jump_analyzer.py` using local imports only; no package install, clone, download, or external API.
- **Routing**: No special runtime routing.
- **Judgment**: Local computer-vision scripting task.

### `vulnerable-secret`

- **Runtime need**: None confirmed.
- **Exact runtime downloads**:
  - `none`: the agent interacts with the local executable and writes the extracted flag.
  - Local/preinstalled resources: `/app/vulnerable`, shell tools `grep`/`sed`, Python 3 standard library `sys`, and `/app/results.txt` output path.
  - Runtime exploit input: a local payload of `64` bytes plus little-endian authenticated flag overwrite `\x01\x00\x00\x00`.
  - No APT package, PyPI package, Git clone, model checkpoint, dataset, or external API is required during the agent attempt. The C source compilation with `gcc` happens during environment construction.
- **Evidence**:
  - `instruction.md`: vulnerable executable is provided locally; output is `/app/results.txt`.
  - `README.md`: installed tools are gcc and Python; analysis targets the local stripped binary.
  - `solution/solve.sh`: interacts with `./vulnerable`, crafts a payload with Python standard library, and extracts local output; no package install, clone, download, or external API.
- **Routing**: No special runtime routing.
- **Judgment**: Local binary exploitation/reverse-engineering task.

### `winning-avg-corewars`

- **Runtime need**: None confirmed.
- **Exact runtime downloads**:
  - `none`: the agent writes local Redcode to `/app/my_warrior.red` and can test locally with preinstalled `pmars`.
  - Local/preinstalled resources: `/usr/local/bin/pmars` or `pmars` on PATH, `/app/warriors/stone.red`, `/app/warriors/paper.red`, `/app/warriors/vampire.red`, `/app/warriors/snake.red`, and `/app/warriors/g2-clear.red`.
  - Fixed simulator parameters: core size `8000`, max cycles `80000`, test command `pmars -b -r 100 -f my_warrior.red warriors/<opponent>.red`.
  - No pMARS source download, opponent download, APT package, PyPI package, Git clone, model checkpoint, dataset, or external API is required during the agent attempt. Source URLs in comments are not executed runtime dependencies.
- **Evidence**:
  - `instruction.md`: pMARS is pre-installed/configured and opponent warriors are local in `/app/warriors/`.
  - `README.md`: simulator and five classic opponents are pre-installed.
  - `solution/solve.sh`: writes `my_warrior.red` directly; no package install, clone, download, or external API.
- **Routing**: No special runtime routing.
- **Judgment**: Local Redcode strategy/programming task.

### `write-compressor`

- **Runtime need**: None confirmed.
- **Exact runtime downloads**:
  - `none`: the agent writes a local compressor, compiles it, and generates `/app/data.comp`.
  - Local resources: `/app/decomp.c`, `/app/decomp`, and `/app/data.txt`.
  - Local/preinstalled tools: `gcc`, `rustc`, `bc`, shell, and standard Rust library.
  - Local generated files: `/app/main.rs` or equivalent compressor source, compiled compressor binary, and `/app/data.comp` with size at most `2500` bytes.
  - No APT package, PyPI package, Cargo crate download, Git clone, model checkpoint, dataset, or external API is required during the agent attempt.
- **Evidence**:
  - `instruction.md`: `/app/decomp.c`, `/app/decomp`, and `/app/data.txt` are provided locally; required output is `/app/data.comp`.
  - `README.md`: `gcc`, `rustc`, and `bc` are installed tools in the environment.
  - `solution/solve.sh`: writes a local Rust compressor, compiles/runs it with pre-installed tooling, and produces `data.comp`; a keyword scan found no `apt`, `pip`, `wget`, `curl`, `git clone`, `http`, `cargo`, or similar external access.
- **Routing**: No special runtime routing.
- **Judgment**: Local reverse-engineering/compression implementation task.
