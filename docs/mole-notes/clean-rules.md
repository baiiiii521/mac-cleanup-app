# Mole 可清理项规则 —— 供 Python 实现的规格说明

> 分析对象：`/tmp/mole`（Mole v1.61.0，commit `8c55e8c`，GPLv3）
> 分析日期：2026-10-18
> 方法：grep 定位规则表 + 逐段精读。本文每条结论都标注 `文件:行号`。源码里**没有**的规则，本文明确写「源码中不存在」，不做推测。
> 配套文档：`protection.md`（保护规则/归属解析）。本文只讲「删什么、什么条件才删」。

---

## 0. 全局事实（Python 实现前必读）

### 0.1 保留期常量（`lib/core/base.sh:148-160`）

```bash
MOLE_TEMP_FILE_AGE_DAYS=7       # 临时文件
MOLE_ORPHAN_AGE_DAYS=30         # 孤儿数据
MOLE_DOTDIR_ORPHAN_AGE_DAYS=60  # 点目录孤儿提示
MOLE_MAIL_AGE_DAYS=30           # 邮件附件
MOLE_LOG_AGE_DAYS=7             # 日志
MOLE_CRASH_REPORT_AGE_DAYS=7    # 崩溃报告
MOLE_SAVED_STATE_AGE_DAYS=30    # 保存的应用状态
MOLE_GPU_CACHE_AGE_DAYS=1       # 可重建 GPU 缓存
MOLE_TM_BACKUP_SAFE_HOURS=48    # Time Machine 安全窗（小时）
MOLE_MAX_DS_STORE_FILES=500     # 单次扫描 .DS_Store 上限
MOLE_MAX_ORPHAN_ITERATIONS=100
```

### 0.2 超时常量（`lib/core/timeouts.sh:61-67`）

```bash
QUICK_DETECT=2s  SHORT_QUERY=3s  MEDIUM_PROBE=5s
PKG_LIST=10s     PKG_CLEANUP=20s  DISK_VERIFY=30s  HINT_SCAN=15s
```

全部可通过同名环境变量覆盖。Python 实现建议直接用同名常量。

### 0.3 `safe_clean` 的 5 道过滤（`bin/clean.sh:819-1447`）

每个目标在真正删除前依次经过：

| 顺序 | 检查 | 位置 | 命中后行为 |
|---|---|---|---|
| 1 | `should_protect_path` | `bin/clean.sh:900` | SKIPPED "protected" |
| 2 | `is_path_whitelisted` | `bin/clean.sh:908` | SKIPPED "whitelist" |
| 3 | `holds_compiled_model_cache` | `bin/clean.sh:915` | SKIPPED "compiled model cache" |
| 4 | `normalize_paths_for_cleanup`（去嵌套） | `bin/clean.sh:927-938` | 父目录已入选则丢弃子目录 |
| 5 | `safe_remove` 内部重跑 1/2/3 + `validate_path_for_deletion` | `file_ops.sh:1451-1500` | 拒绝 |

**关键事实**：`safe_clean` 永远返回 0（除信号）；`safe_clean_guarded` 在被 guard 拒绝时返回 75（`bin/clean.sh:1453-1458`）。Python 实现里建议把「跳过」和「失败」分开统计，Mole 用 `skipped_count` / `removal_failed_count` 两个计数器（`bin/clean.sh:872`）。

### 0.4 三段式返回值约定（贯穿全仓）

所有探测函数是**三态**：`0=确认是`、`1=确认否`、`2/≥128=不知道`。**「不知道」一律按「危险」处理**。例：

- `mole_pgrep_any`（`base.sh:72-94`）：任一 pattern 命中返回 0；全部 exit 1 返回 1；任一 pgrep 异常返回 2。
- `mole_clean_process_guard`（`base.sh:1710-1723`）：`1` 才放行，`0` 记为「正在运行」，`2` 记为「process state unknown」。
- `_mole_paths_have_open_handle`（`file_ops.sh:760-798`）：lsof exit 1 且 stderr 为空才算「确认无句柄」；有诊断信息降级为 2。

### 0.5 `clean` 的执行顺序（`bin/clean.sh:1736-1840`）

```
1  System          clean_deep_system → clean_local_snapshots
2  User essentials clean_user_essentials → clean_finder_metadata
3  App caches      clean_app_caches
4  Browsers        clean_browsers
5  Cloud & Office  run_cloud_and_office_cleanup
6  Developer tools clean_developer_tools
7  Apps & utilities clean_user_gui_applications
8  Virtualization  clean_virtualization_tools
9  App Support     clean_application_support_logs
10 App leftovers   clean_orphaned_app_data → clean_orphaned_system_services
                   → clean_orphaned_container_stubs → show_user_launch_agent_hint_notice
11 Apple Silicon   clean_apple_silicon_caches
12 Firmware        clean_cached_device_firmware
13 Time Machine    clean_time_machine_failed_backups
14 Large files     check_large_file_candidates（只读报告）
15 Project artifacts show_project_artifact_hint_notice（只读提示）
```

注意 **第 2 段先于第 6 段**：`~/Library/Caches/*` 的整体清扫发生在开发者缓存之前，所以 `~/Library/Caches/deno` 被显式排除（`user.sh:250-302`）。

---

## 1. 开发缓存与构建产物规则表

### 1.1 统一入口与四个删除通道

`clean_developer_tools`（`dev.sh:5929-5979`）按固定顺序跑 33 步（32 个经 `_run_developer_cleanup_step` 包裹的函数步 + 1 处直接 `safe_clean` Homebrew downloads；其中 `clean_dev_mobile`、`clean_dev_ai_agents`、`clean_xcode_tools` 三步标 `--strict`）。每步由 `_run_developer_cleanup_step`（`dev.sh:5895-5926`）包裹：超时/信号向上抛，普通失败只记 debug，`--strict` 步失败才中断。

通道 A：`safe_clean <glob> "说明"` —— 直接按 glob 删。
通道 B：`clean_tool_cache "说明" <cache_path> <owner 命令>`（`dev.sh:12-74`）—— 优先交给工具自己清：
- 先查白名单，命中则 skip；
- `DRY_RUN` 下只打印；
- 否则执行 owner 命令，**失败不降级为直接删除**（注释 `dev.sh:53-56`：`Do not bypass its result with direct deletion of the same root`）。

通道 C：`_dev_safe_clean_process_guarded <probe> <family> <说明> <targets...> <label>`（`dev.sh:1777-1802`）—— 带进程守卫，guard 拒绝返回 1。

通道 D：`clean_guarded_dev_cache_root <容器> <缓存根> <probe> <family> <说明> <targets...>`（`dev.sh:693-753`）—— 在 C 的基础上再加物理路径包含性校验（`guarded_dev_cache_root_physical_path`，`dev.sh:609-642`）。

### 1.2 Node / Bun / Deno

| # | 路径模式 | 判定可删条件 | 说明文案 | 来源 |
|---|---|---|---|---|
| N1 | `$(npm config get cache)`（默认 `~/.npm`） | `npm` 存在且 `npm config get cache` 成功返回绝对路径 | `npm cache` | `dev.sh:483-491` |
| N2 | `~/.npm/{_cacache,_npx,_logs,_prebuilds}/*` | 无条件（`npm cache clean --force` 清不掉这些） | `npm cache directory` / `npm npx cache` / `npm logs` / `npm prebuilds` | `dev.sh:499-507` |
| N3 | `<custom npm cache>/{_cacache,_npx,_logs,_prebuilds}/*` | 仅当 custom 路径 ≠ 默认路径（用 `pwd -P` 归一化后比较） | 同上 + ` (custom path)` | `dev.sh:509-523` |
| N4 | `$(pnpm store path)`（默认 `~/Library/pnpm/store`） | ① 无 pnpm 进程（`pnpm_process_blocks_prune`，`dev.sh:343-359`：`pgrep -f '(^\|/)pnpm(\.cjs)?([[:space:]]\|$)'`，非 1 一律阻断）② 已安装的 pnpm 可执行且 `--version` 成功 ③ store path 绝对、无 `..`/控制字符（`dev.sh:363-372`） | `pnpm cache`（执行 `pnpm store prune`，无 `--force`） | `dev.sh:408-477` |
| N5 | `$COREPACK_HOME` 或 `~/.cache/node/corepack` | 路径必须绝对且不是 `/`、`$HOME`、`~/Library`；`corepack --version` 成功则用 `corepack cache clean`，否则 `safe_clean` | `Corepack cache` | `dev.sh:76-96` |
| N6 | `$(bun pm cache)`（默认 `~/.bun/install/cache`） | `bun --version` 成功 → `bun pm cache rm`；失败则回退 `safe_clean "$bun_cache_path"/*` | `bun cache` / `Bun cache` | `dev.sh:524-598` |
| N7 | `~/.bun/install/cache/*` | custom 路径 ≠ 默认路径时，把默认路径当孤儿清 | `Orphaned bun cache` | `dev.sh:586-590` |
| N8 | `~/.tnpm/_cacache/*`、`~/.tnpm/_logs/*` | 无条件 | `tnpm cache directory` / `tnpm logs` | `dev.sh:601-602` |
| N9 | `~/.yarn/cache/*`、`~/Library/Caches/Yarn/*` | 无条件 | `Yarn cache` / `Yarn v1 cache` | `dev.sh:603-604` |
| N10 | `$DENO_DIR`（默认 `~/Library/Caches/deno`） | **不删**。从 `~/Library/Caches/*` 整体清扫里剔除，只在 Large files 里报告 | （review-only） | `base.sh:305-323`、`user.sh:250-302`、`dev.sh:4134-4136` |

> **实现要点**：`_user_cache_deno_delete_guard`（`user.sh:156-189`）在删除边界**重新解析** DENO_DIR，候选路径等于它、在它里面、或**包含**它，一律拒绝。这是防「扫描时 DENO_DIR 指向 A，删除时改指 B」的 TOCTOU。

### 1.3 前端构建缓存（`clean_dev_frontend`，`dev.sh:1406-1418`）

全部无条件 `safe_clean`，无进程守卫：

```
~/.cache/typescript/*     TypeScript cache
~/.cache/electron/*       Electron cache
~/.cache/node-gyp/*       node-gyp cache
~/.node-gyp/*             node-gyp build cache
~/.turbo/cache/*          Turbo cache
~/.vite/cache/*           Vite cache
~/.cache/vite/*           Vite global cache
~/.cache/webpack/*        Webpack cache
~/.parcel-cache/*         Parcel cache
~/.cache/eslint/*         ESLint cache
~/.cache/prettier/*       Prettier cache
```

另加：`~/.cache/prisma/*`（`dev.sh:5832`）、`~/.cache/opencode/*`（`dev.sh:5834`）、`~/.cache/bazel/*`、`~/.cache/zig/*`（`dev.sh:4132-4133`）。

### 1.4 Python

| # | 路径 | 条件 | 文案 | 来源 |
|---|---|---|---|---|
| P1 | `$(pip3 cache dir)` | `pip3 --version` 成功（排除 macOS CLT stub） | `pip cache`（`pip3 cache purge`） | `dev.sh:829-835` |
| P2 | `~/.pyenv/cache/*` | 无条件 | `pyenv cache` | `dev.sh:837` |
| P3 | `~/.cache/poetry/*` | 无条件 | `Poetry cache` | `dev.sh:838` |
| P4 | `~/Library/Caches/pypoetry/artifacts/*` | 无条件 | `Poetry artifacts cache` | `dev.sh:846` |
| P5 | `~/Library/Caches/pypoetry/cache/*` | 无条件 | `Poetry package cache` | `dev.sh:847` |
| P6 | `~/Library/Caches/pypoetry/virtualenvs/*` | **永不删**（安全白名单，`base.sh:210`） | — | `base.sh:193-212` |
| P7 | `$(uv cache dir)`（默认 `~/.cache/uv`） | `uv --version` 成功 → `uv cache prune`；`uv cache dir` 失败则回退 `safe_clean ~/.cache/uv/*` | `uv cache` | `dev.sh:98-111` |
| P8 | `~/.cache/ruff/*` | 无条件 | `Ruff cache` | `dev.sh:849` |
| P9 | `~/.cache/mypy/*` | 无条件 | `MyPy cache` | `dev.sh:850` |
| P10 | `~/.pytest_cache/*` | 无条件 | `Pytest cache` | `dev.sh:851` |
| P11 | `~/Library/Application Support/pyinstaller/bincache*` | 无 PyInstaller 进程（`pgrep -x pyinstaller` / `-f "[p]yinstaller"` / `-f "[P]yInstaller"`）+ 物理包含性校验 | `PyInstaller binary cache` | `dev.sh:755-783` |
| P12 | `~/.jupyter/runtime/*` | 无条件 | `Jupyter runtime cache` | `dev.sh:853` |
| P13 | conda `pkgs_dirs`（默认 `~/.conda/pkgs`、`~/anaconda3/pkgs`、`~/miniconda3/pkgs`、`~/miniforge3/pkgs`、`~/mambaforge/pkgs`） | `conda --version` 成功且 `conda info --json` 解析出 `pkgs_dirs` → `conda clean --yes --index-cache --tarballs --logfiles`；conda 不可用则**只记日志不删** | `conda index/tarball/log caches` | `dev.sh:282-330` |

**明确不删**（`dev.sh:854-858` 注释）：`~/.cache/huggingface`、PyTorch、TensorFlow、Weights & Biases 的根目录。理由：HF 自己的 prune 都会警告中断可能损坏缓存；其余根目录混着可复用产物与运行态。

项目级 Python 缓存走另一条路（`clean_project_caches`），见 §1.9。

### 1.5 Rust

| # | 路径 | 条件 | 文案 | 来源 |
|---|---|---|---|---|
| R1 | `${CARGO_HOME:-~/.cargo}/registry/cache/*` | ① 无 `cargo`/`rustc`/`rustdoc`/`clippy-driver`/`cargo-nextest` 进程（`dev.sh:1171-1178`）② 缓存根物理上仍在 CARGO_HOME 内（`rust_cache_root_physical_path`，`dev.sh:1183-1199`） | `Rust cargo cache` | `dev.sh:1273-1294` |
| R2 | `${RUSTUP_HOME:-~/.rustup}/downloads/*` | 无条件 | `Rustup downloads cache` | `dev.sh:1294` |

**明确不删**（`dev.sh:1262-1272` 注释）：`registry/src`（解压后的 crate 源码，rust-analyzer 持续读）、`registry/index`、`git`、`bin`、`toolchains`。理由：删掉 `registry/src` 会让所有离线构建变成 crates.io 往返；Cargo 1.88+ 自己做 age-aware GC。

`CARGO_HOME` / `RUSTUP_HOME` 由 `resolve_tool_home`（`dev.sh:1152-1168`）解析：只接受绝对路径、拒绝 `..`、`\n`、`\r`，否则回落默认值。

`check_rust_toolchains`（`dev.sh:1335`）只做**多版本提示**，不删。

### 1.6 Go

| # | 路径 | 条件 | 文案 | 来源 |
|---|---|---|---|---|
| G1 | `go env GOMODCACHE`（默认 `~/go/pkg/mod`） | ① `go` 存在 ② 无 `go`/`gopls` 进程（`dev.sh:862-869`）③ 物理根不是 `/`、`$HOME`、`~/Library`、`~/Library/Caches`、`~/.cache`、`~/go`（`dev.sh:876-901`）④ uid 属于调用者 → `go clean -modcache` | `Go module cache` | `dev.sh:1072-1104` |
| G2 | `go env GOCACHE`（默认 `~/Library/Caches/go-build`） | **无进程守卫**（Go 文档声明 GOCACHE 支持多进程并发）→ `go clean -cache` | `Go build cache` | `dev.sh:862-869`、`dev.sh:1100-1103` |

`_run_go_cache_clean_bound`（`dev.sh:903-...`）在跑 owner 命令前**重读 link bit**：GOMODCACHE 是符号链接就拒绝（注释 `dev.sh:920-925`：`go clean -modcache` 会删链接指向的东西）。

项目级：`vendor/` 在 purge 中被保护（`project.sh:473-497`，检测到 `go.mod` 就 return 0=保护）。

### 1.7 Java / Gradle / Maven / Android

| # | 路径 | 条件 | 文案 | 来源 |
|---|---|---|---|---|
| J1 | `~/.gradle/{notifications,daemon,workers}/*`（跳过 `.` 开头） | ① `~/.gradle` 是真实目录非链接 ② 无 Gradle daemon（`pgrep -f "org.gradle.launcher.daemon"` 或 `-f "GradleDaemon"`，`dev.sh:334-341`）③ 未被白名单 | `Gradle notifications cache` / `Gradle daemon/workers` | `dev.sh:3016-3140` |
| J2 | `~/.gradle/caches/build-cache-*/*`（跳过 `.` 开头） | 同上 | `Gradle build cache` | `dev.sh:3016-3140` |
| J3 | `~/Library/Caches/Google/AndroidStudio*/*` | 无条件 | `Android Studio cache` | `dev.sh:2993` |
| J4 | `~/.android/build-cache/*` | 无条件 | `Android build cache` | `dev.sh:2996` |
| J5 | `~/.android/cache/*` | 无条件 | `Android SDK cache` | `dev.sh:2997` |
| J6 | `~/Library/Android/sdk/ndk` 多版本 | **只提示不删**（`check_multiple_versions`，`dev.sh:1420-1426`） | `Android NDK versions` | `dev.sh:1420` |

**明确不删**（`dev.sh:3018-3026` 注释）：
- `~/.m2/repository`、`~/.ivy2/cache` —— Maven/sbt/Ivy 的解析目标，等同「还原仓库」，不是 HTTP 缓存；
- `~/.sbt/boot`、`~/.sbt/launchers` —— 装的是 Scala 编译器和 sbt launcher jar；
- 三者只在 `check_large_file_candidates`（`user.sh:3233`）里作为 Large files 报告。

> **重要设计变更**：Maven 以前在这里删，靠 `DEFAULT_WHITELIST_PATTERNS` 兜底。但白名单是「整文件替换」语义（用户存一条自定义就没了），所以直接把删除路径砍掉（`dev.sh:3023-3025`）。**Python 实现不要给 `~/.m2` 开删除口子**。

默认白名单另有两项（`base.sh:170-171`）：`~/.gradle/caches/*`、`~/.gradle/daemon/*` —— 所以 J2 默认其实是被白名单挡住的，只有用户主动取消才生效。

### 1.8 Xcode / Swift

| # | 路径 | 条件 | 文案 | 来源 |
|---|---|---|---|---|
| X1 | `~/Library/Developer/Xcode/DerivedData/*` | ① 逐项目过滤 protected/whitelist/compiled-model ② 无 `Xcode`/`xcodebuild`/`xctest`/`XCTRunner`/`XCBBuildService`/`swift-frontend`（`app_protection.sh:35-48`）③ **size 探测前后各查一次进程** | `Xcode DerivedData · N projects, <size>` | `app_caches.sh:95-260` |
| X2 | `~/Library/Developer/Xcode/Products/*` | 同上 | `Xcode build products` | `app_caches.sh:305, 346` |
| X3 | `~/Library/Caches/com.apple.dt.Xcode/*` | 同上 | `Xcode cache` | `app_caches.sh:317-319` |
| X4 | `~/Library/Developer/XCTestDevices/*`（逐个一级子项） | 无 Xcode 工具进程；根目录本身保留 | `Xcode XCTestDevices test data` | `dev.sh:1867-1920` |
| X5 | `/Library/Developer/CoreSimulator/Caches/*`（需 sudo） | 无 Xcode/Simulator/xcodebuild/xctest/XCTRunner/simctl 进程，且 `simctl list devices booted -j` 无 `"udid"` | `Xcode Simulator system cache` | `dev.sh:1922-...` |
| X6 | `~/Library/Developer/CoreSimulator/Caches/*`、`~/Library/Developer/CoreSimulator/Devices/*/data/tmp/*`、`~/Library/Logs/CoreSimulator/*` | 同上 | `Simulator cache` / `Simulator temp files` / `CoreSimulator logs` | `app_caches.sh:262-296` |
| X7 | `~/Library/Developer/Xcode/{iOS,macOS,watchOS,tvOS,visionOS,xrOS} DeviceSupport/*` | 按 mtime 降序，**保留最新 2 个**（`MOLE_XCODE_DEVICE_SUPPORT_KEEP`，默认 2）；保留目录内 `Symbols/System/Library/Caches/*` 与 `*.log` 也清 | `iOS DeviceSupport · removed N old versions` | `dev.sh:2113-2420` |
| X8 | `~/Library/Developer/Xcode/UserData/IB Support/*` | 无 Xcode 工具进程 | `Xcode Interface Builder cache` | `dev.sh:2998-3002` |
| X9 | `~/Library/Developer/CoreSimulator/Profiles/Runtimes/*/Contents/Resources/RuntimeRoot/System/Library/Caches/*` | 无 Simulator 进程 | `Simulator runtime cache` | `dev.sh:2988-2992` |
| X10 | `/Library/Developer/Xcode/DocumentationCache/DeveloperDocumentation*.index`（需 sudo） | 按 mtime 降序，**只保留最新 1 个** index 目录；至少 2 个才动手 | `Xcode documentation cache (old indexes)` | `dev.sh:1427-1530` |
| X11 | `xcrun simctl delete unavailable` | 只删 `simctl list devices unavailable` 报出的 UDID；`simctl` 解析状态必须是 `ready` | `Xcode unavailable simulators · removed N` | `dev.sh:2734-2900` |
| X12 | `~/.cache/swift-package-manager/*` | 无条件 | `Swift package manager cache` | `dev.sh:3003` |
| X13 | `~/Library/Caches/org.swift.swiftpm/*` | 无条件 | `Swift package manager library cache` | `dev.sh:3004` |

**不删**：`~/Library/Developer/Xcode/Archives`（只在 Large files 报告，`user.sh:3085`）、`~/Library/Developer/Xcode/DerivedData` 的**全局**版本在 purge 中被保护（`project.sh:561-566`：`[[ "$path" == *"/Library/Developer/Xcode/DerivedData"* ]] && return 0`）。

Simulator 进程探针（`dev.sh:1602-1630`）用 `pgrep -x` 依次查 `Xcode|Simulator|xcodebuild|xctest|XCTRunner|simctl`；`CoreSimulatorService` / `simdiskimaged` 故意**不算**（launchd 常驻，不代表活跃，见注释 `dev.sh:1600-1609`）。

### 1.9 项目级构建产物（`mo purge` + `clean_project_caches`）

#### A. `mo purge` 的候选名清单（`purge_shared.sh:18-53`）

```python
PURGE_TARGETS = [
    "node_modules", "target", "build", "dist", "venv", ".venv",
    ".pytest_cache", ".mypy_cache", ".tox", ".nox", ".ruff_cache",
    ".gradle", ".terragrunt-cache", "__pycache__", ".next", ".nuxt",
    ".output", "vendor", "bin", "obj", ".turbo", ".parcel-cache",
    ".dart_tool", ".zig-cache", "zig-out", ".angular", ".svelte-kit",
    ".astro", "coverage", "DerivedData", "Pods", ".cxx", ".expo", ".build",
]
```

注释里的生态归属：`target`=Rust/Maven，`build`=Gradle/通用，`dist`=JS，`vendor`=PHP Composer，`bin`=.NET，`obj`=C#/Unity，`.cxx`=React Native Android NDK，`.build`=Swift PM。

#### B. 搜索根（`purge_shared.sh:55-73`）

```python
DEFAULT_SEARCH_PATHS = [
    "~/www", "~/dev", "~/Projects", "~/GitHub", "~/Code", "~/Workspace",
    "~/Repos", "~/Development", "~/Library/CloudStorage",
    "~/.codex/worktrees", "~/.claude/worktrees",
]
```
用户配置：`~/.config/mole/purge_paths`（`project.sh:25`，解析见 `purge_shared.sh:294-307`，支持 `~` 展开与 `#` 注释）。

#### C. 项目根判定（`purge_shared.sh:75-103`、`148-165`）

```python
MONOREPO_INDICATORS = ["lerna.json", "pnpm-workspace.yaml", "nx.json", "rush.json", ".git"]
PROJECT_INDICATORS  = ["package.json", "Cargo.toml", "go.mod", "pyproject.toml",
                       "requirements.txt", "pom.xml", "build.gradle", "terragrunt.hcl",
                       "Gemfile", "composer.json", "pubspec.yaml", "Package.swift",
                       "Makefile", "build.zig", "build.zig.zon", ".git"]
```

#### D. 扫描参数

| 参数 | 值 | 来源 |
|---|---|---|
| `MIN_AGE_DAYS` | 7 | `project.sh:17` |
| 最小深度 | 1 | `project.sh:19` |
| 最大深度 | 6 | `project.sh:20` |
| 单根扫描预算 | `MO_PURGE_SCAN_TIMEOUT_SEC`，默认 60s | `project.sh:630` |
| 目录大小预算 | `MO_PURGE_SIZE_TIMEOUT_SEC`，默认 15s | `project.sh:1185` |
| 活动探测预算 | `MO_PURGE_ACTIVITY_TIMEOUT_SEC`，默认 `MEDIUM_PROBE`=5s | `project.sh:1071-1077` |

扫描时 **prune** 的目录（`project.sh:806`）：`.git`、`Library`、`.Trash`、`Applications`；云盘根目录额外用 `-flags +dataless` 跳过在线占位目录（`project.sh:584-596`、`816-830`）。有 `fd` 就用 `fd --hidden --no-ignore --prune --exclude .git --exclude Library --exclude .Trash --exclude Applications`（`project.sh:700-760`），失败回退 `/usr/bin/find`。

另外会把带 **`CACHEDIR.TAG`** 的目录也当候选：签名必须是 `Signature: 8a477f597d28d172789f06886806bc55`（`purge_shared.sh:105-106`、`245-253`）。

#### E. 判定可删的完整闸门（见 §2 详述）

#### F. `clean_project_caches`（`caches.sh:996-1180`）—— 与 purge 不同，这是 `mo clean` 里自动跑的

只认三种名字（`caches.sh:391-393`）：

| 名字 | 动作 | 文案 | 来源 |
|---|---|---|---|
| `.next` | 只清 `<proj>/.next/cache/*`（逐个 child） | `Next.js build cache` | `caches.sh:834-840` |
| `__pycache__` | 同项目根下的 `__pycache__` 聚合成一组批量删；**必须有 `*.pyc`/`*.pyo`** | `Python bytecode cache · <root> · N dirs, <size>` | `caches.sh:323-338`、`882-994` |
| `.dart_tool` | 清 `.dart_tool` 本身；**且**同目录 `build/` 在「无嵌套 git 仓库」时也清 | `Flutter build cache (.dart_tool)` / `Flutter build cache (build/)` | `caches.sh:846-875` |

扫描参数（`caches.sh:373-400`）：`-maxdepth 9 -mount`，prune 列表含 `Library .Trash node_modules .git .svn .hg .venv venv .pnpm-store .fvm DerivedData Pods miniconda3 anaconda3 miniforge3 mambaforge site-packages target .build .gradle Index.noindex _cacache`；单根预算 `MOLE_PROJECT_CACHE_SCAN_TIMEOUT` 默认 6s；发现根时 `MOLE_PROJECT_CACHE_DISCOVERY_TIMEOUT` 默认 2s。

### 1.10 Flutter / Dart

| 项 | 结论 | 来源 |
|---|---|---|
| `.dart_tool` | 清（见 §1.9F），需 git 未跟踪 | `caches.sh:846-866` |
| 同目录 `build/` | 仅在 `.dart_tool` 旁边、且 `build/` 内无 `.git` 时清 | `caches.sh:867-875` |
| `~/.pub-cache/*` | **不删**（`# safe_clean ~/.pub-cache/*` 被注释） | `dev.sh:4131` |
| `~/.cache/flutter/*` | **不删**（注释掉） | `dev.sh:2995` |
| `~/fvm/versions` | 只报告（`fvm remove` 归 fvm 管） | `user.sh:3558-3560` |
| `.fvm` | 扫描时 prune | `caches.sh:389` |
| `.expo/*` | 清 7 个子目录，但**保留 `state.json`**（含 auth token） | `dev.sh:3005-3012` |

`.expo` 明细（`dev.sh:3006-3012`）：`expo-go`、`android-apk-cache`、`ios-simulator-app-cache`、`native-modules-cache`、`schema-cache`、`template-cache`、`versions-cache`。

### 1.11 Ruby / Perl / PHP / .NET / Haskell / Elixir / Zig / OCaml

| 生态 | 删 | 不删 | 来源 |
|---|---|---|---|
| Ruby | `~/.rbenv/cache/*`、`~/.gem/specs/*`、`~/.gem/ruby/*/cache/*.gem`、`~/.bundle/cache/*` | 已安装 gem 版本 | `dev.sh:1297-1302` |
| Perl | `~/.cpan/build/*` | `~/.cpan/sources`（发行包存储，跨安装复用） | `dev.sh:1304-1308` |
| PHP | `~/.composer/cache/*`、`~/Library/Caches/composer/*` | — | `dev.sh:4124-4125` |
| .NET | purge 里的 `bin/`（条件见下）、`obj/` | `~/.nuget/packages`（还原目标，等同 `~/.m2`） | `dev.sh:4126-4130`、`project.sh:455-468` |
| Haskell | **无清理阶段** | `~/.stack/programs`（Stack 装的 GHC）、`~/.cabal/packages`（源码 tarball 存储） | `dev.sh:5886-5889` |
| Elixir | `~/.hex/cache/*` | `~/.mix/archives`（已安装 Mix 工具） | `dev.sh:5883-5885` |
| Zig | `~/.cache/zig/*`；purge 的 `.zig-cache`、`zig-out` | — | `dev.sh:4133`、`purge_shared.sh:42-43` |
| OCaml | `~/.opam/download-cache/*` | — | `dev.sh:5891-5893` |

`bin/` 的 .NET 判定（`project.sh:455-468`）三条件全满足才允许清：
1. 目录名恰为 `bin`；
2. 父目录 `-maxdepth 1` 内有 `*.csproj` / `*.fsproj` / `*.vbproj`；
3. `bin/` 下有 `Debug/` 或 `Release/`。

`vendor/` 判定（`project.sh:473-497`）：
- 父目录有 `composer.json` → **可清**（`composer install` 能还原）；
- 父目录是 Rails 根（`config/application.rb` + `Gemfile` + (`bin/rails` 或 `config/environment.rb`)）→ 保护；
- 父目录有 `go.mod` → 保护；
- 其他未知 → **默认保护**。

### 1.12 Unity / Unreal / Godot

| 引擎 | 结论 | 来源 |
|---|---|---|
| Unity | 只清 `~/Library/Caches/com.unity3d.*/*`（`Unity cache`）；项目内 `obj/` 走 purge | `dev.sh:5785`、`purge_shared.sh:38` |
| Unreal | **源码中不存在任何 Unreal 规则**。`grep -rni 'unreal\|uproject\|DerivedDataCache'` 只命中无关的 "intermediate states" 注释 | — |
| Godot | 只有**卸载**关联（`app_protection.sh:1719-1721` 把 `~/Library/Application Support/Godot` 列为随 app 一起删的路径）；`mo clean` 里**没有** Godot 缓存规则 | `app_protection.sh:1719` |

> 结论：不要凭空给 Unreal/Godot 编规则。`build/`、`intermediate/` 之类名字在 Mole 里没有专门分支。

### 1.13 容器 / Docker / OrbStack / 虚拟机

| # | 对象 | 结论 | 来源 |
|---|---|---|---|
| C1 | Docker 引擎数据 | **不删**，只打印 `Docker unused data · review with docker system df` | `dev.sh:1358-1364` |
| C2 | OrbStack 数据（`~/Library/Group Containers/*dev.orbstack/data`） | **不删**，只算大小并提示 `docker system df`；且 `is_orbstack_runtime_path` 把这些路径设为硬保护 | `dev.sh:1348-1384`、`app_protection.sh:296-315` |
| C3 | `~/.docker/buildx/cache/*` | 删 | `dev.sh:1385` |
| C4 | `~/.local/share/containers/storage/tmp/*` | 删 | `dev.sh:1400` |
| C5 | `~/Library/Caches/lima/download/by-url-sha256/*` | 删 | `user.sh:2395` |
| C6 | `~/.vagrant.d/tmp/*` | 删 | `user.sh:2396` |
| C7 | `~/VirtualBox VMs/.cache` | 删 | `user.sh:2394` |
| C8 | `~/Library/Caches/com.vmware.fusion`、`~/Library/Caches/com.parallels.*` | 删 | `user.sh:2391-2392` |
| C9 | `~/Library/Caches/com.utmapp.UTM/*`、`~/Library/Containers/com.utmapp.UTM/Data/{Library/Caches,tmp}/*` | 无 UTM 进程时删 | `user.sh:2276-2292` |
| C10 | `~/.tart/cache` | `tart prune --entries caches --older-than 30`（30 天） | `user.sh:2294-2372` |
| C11 | `~/Library/Containers/com.docker.docker/Data`、`~/OrbStack`、`~/.lima` | Large files 只报告 | `user.sh:3086-3090` |

### 1.14 AI 模型 / AI 编码代理缓存

| # | 路径 | 条件 | 文案 | 来源 |
|---|---|---|---|---|
| A1 | `~/.ollama/models/*` | **默认白名单，永不删** | — | `base.sh:172` |
| A2 | `~/.cache/huggingface`（或 `$HF_HOME`） | **只报告**（`huggingface-cli` 管） | `Hugging Face cache` | `user.sh:3069-3070`、`3550-3551` |
| A3 | `~/.lmstudio/models` | **只报告** | `LM Studio models` | `user.sh:3087`、`3479` |
| A4 | `~/Library/Caches/com.lmstudio.lmstudio/*` | 删 | `LM Studio cache` | `app_caches.sh:885` |
| A5 | `~/.local/share/claude/versions/*` | 保留最近 `MOLE_AI_AGENTS_KEEP`（默认 1）个 + `~/.local/bin/claude` 指向的那个 | `Claude Code old version` | `dev.sh:4074-4096` |
| A6 | `~/.local/share/cursor-agent/versions/*` | 同上，active symlink `~/.local/bin/cursor-agent` | `Cursor Agent old version` | `dev.sh:4080` |
| A7 | `~/.copilot/pkg/universal/*` | 同上，active symlink `~/.local/bin/copilot` | `GitHub Copilot CLI old version` | `dev.sh:4081` |
| A8 | `~/.local/share/cursor-agent/*.log` | `-mtime +7` | （无独立行） | `dev.sh:5852` |
| A9 | `~/Library/Caches/Codex/Default/{Cache,Code Cache}/*` 等 6 个叶子 | 无 Codex/ChatGPT 进程；只删 Chromium 可重建叶子，profile 本身保留 | `Codex Desktop Cache` / `Codex Desktop Code Cache` | `dev.sh:4232-4296` |
| A10 | `~/.cache/codex-runtimes/*` | 无 Codex 进程 + 该 runtime 目录「过期」（名字含 `tmp*/temp*/*.tmp/incomplete*/partial*`，或无 `runtime.json` 且无 `dependencies`） | `Codex CLI runtimes` | `dev.sh:5098-5120`、`5160-5250` |
| A11 | `~/Library/Application Support/Codex/Crashpad/pending/*/*` | `-mtime +30` + 无 Codex 进程 + 无 crashpad handler + 无 open handle | `Codex crash reports` | `dev.sh:4935-5075` |
| A12 | `~/.codex/.tmp` 市场 staging | `-mtime +30` | `Codex marketplace staging` | `dev.sh:5318-5450` |
| A13 | Codex daemon `releases/*` | 需持 `install.lock`（非阻塞 `lockf`） | `Codex app-server daemon releases` | `dev.sh:4707-4884` |
| A14 | `~/Library/Application Support/Claude/{Cache,Code Cache,GPUCache,DawnGraphiteCache,DawnWebGPUCache,sentry}/*` | 无条件 | `Claude cache` 等 | `dev.sh:5813-5818` |
| A15 | `~/Library/Caches/com.openai.chat/*`、`~/Library/Caches/com.anthropic.claudefordesktop/*`、`~/Library/Logs/Claude/*` | 无条件 | `ChatGPT cache` / `Claude desktop cache` / `Claude logs` | `app_caches.sh:882-884` |
| A16 | `~/Library/Application Support/Codex`、`~/.claude`、`~/.codex` | **不删**（持久记忆/插件注册/hooks/session 上下文） | — | `dev.sh:5861-5863`、`5253-5260` |
| A17 | `~/Library/Caches/Google/Chrome/OptGuideOnDeviceModel/*`、`OptGuideOnDeviceClassifierModel/*`、`optimization_guide_model_store/*` | Chrome 未运行时删 | `Chrome on-device model cache` 等 | `user.sh:777-781` |
| A18 | `com.apple.e5rt.e5bundlecache` | **硬保护**（`holds_compiled_model_cache`） | — | `app_protection.sh:325-335` |
| A19 | `~/.gemini/antigravity-browser-profile/*` | 无 Antigravity/Gemini 进程 | `Antigravity cache` 等 | `dev.sh:5496-5545` |
| A20 | `~/.gemini/tmp` | **不删**（名字骗人，存的是 gemini-cli 会话检查点与 prompt 历史） | — | `dev.sh:5543-5544` |

### 1.15 其他开发工具（一行一规则，全部无条件）

```
~/.kube/cache/*                               Kubernetes cache            dev.sh:1399
~/.aws/cli/cache/*                            AWS CLI cache               dev.sh:1401
~/.config/gcloud/logs/*                       Google Cloud logs           dev.sh:1402
~/.azure/logs/*                               Azure CLI logs              dev.sh:1403
~/.cache/terraform/*                          Terraform cache             dev.sh:4141
~/.grafana/cache/*                            Grafana cache               dev.sh:4142
~/.jenkins/workspace/*/target/*               Jenkins workspace cache     dev.sh:4145
~/.cache/gitlab-runner/*                      GitLab Runner cache         dev.sh:4146
~/.github/cache/*                             GitHub Actions cache        dev.sh:4147
~/.circleci/cache/*                           CircleCI cache              dev.sh:4148
~/.sonar/*                                    SonarQube cache             dev.sh:4149
~/.oh-my-zsh/cache/*                          Oh My Zsh cache             dev.sh:5871
~/.cache/pre-commit/*                         pre-commit cache            dev.sh:5872
~/.cache/curl/* ~/.cache/wget/*               curl/wget cache             dev.sh:5876-5877
~/Library/Caches/curl/* ~/Library/Caches/wget/*  macOS curl/wget cache    dev.sh:5878-5879
~/Library/Caches/com.sequel-ace.sequel-ace/*  Sequel Ace cache            dev.sh:4153
~/Library/Caches/com.eggerapps.Sequel-Pro/*   Sequel Pro cache            dev.sh:4154
~/Library/Caches/redis-desktop-manager/*      Redis Desktop Manager       dev.sh:4155
~/Library/Caches/com.navicat.*                Navicat cache               dev.sh:4156
~/Library/Caches/com.dbeaver.*                DBeaver cache               dev.sh:4157
~/Library/Caches/com.redis.RedisInsight       Redis Insight cache         dev.sh:4158
~/Library/Caches/com.postmanlabs.mac/*        Postman cache               dev.sh:4162
~/Library/Caches/com.konghq.insomnia/*        Insomnia cache              dev.sh:4163
~/Library/Caches/com.tinyapp.TablePlus/*      TablePlus cache             dev.sh:4164
~/Library/Caches/com.getpaw.Paw/*             Paw API cache               dev.sh:4165
~/Library/Caches/com.charlesproxy.charles/*   Charles Proxy cache         dev.sh:4166
~/Library/Caches/com.proxyman.NSProxy/*       Proxyman cache              dev.sh:4167
~/Library/Caches/com.mongodb.compass/*        MongoDB Compass cache       dev.sh:5786
~/Library/Caches/com.github.GitHubDesktop/*   GitHub Desktop cache        dev.sh:5788
~/Library/Caches/SentryCrash/*                Sentry crash reports        dev.sh:5789
~/Library/Caches/KSCrash/*                    KSCrash reports             dev.sh:5790
~/Library/Caches/com.crashlytics.data/*       Crashlytics data            dev.sh:5791
~/Library/Application Support/com.wondershare.Installer/*  Wondershare installer payload  dev.sh:5865
```

GitHub CLI（`dev.sh:151-280`）：`$XDG_CACHE_HOME/gh` 或 `~/.cache/gh`，需 `gh` 存在、无 gh 进程、`gh config clear-cache --help` 可用 → `gh config clear-cache`。路径必须绝对、物理父目录非 `/` 与 `$HOME`。

Clang module cache（`dev.sh:785-820`）：`$(getconf DARWIN_USER_CACHE_DIR)/clang/*`，需无 Xcode 工具进程 + 无 `clang`/`clangd`/`swiftc`/`sourcekit-lsp`/`SourceKitService`。

Playwright 泄漏的自动化浏览器（`dev.sh:3934-4070`）：
- 进程侧：`ps -Ao pid=,ppid=,etime=,lstart=,command= -ww` 里 `ppid==1` 且命令行含 `playwright_chromiumdev_profile` → `kill -TERM`，1s 后仍存活且记录匹配 → `kill -9`；
- 磁盘侧：`$(getconf DARWIN_USER_TEMP_DIR)playwright_chromiumdev_profile-*`，`mtime` 距今 ≥ 2h，且 `pgrep -qf "$d"` 返回 1（确认无进程引用）→ 删。

### 1.16 明确列在「不清理」清单里的开发工具

| 对象 | 理由（源码原话要点） | 来源 |
|---|---|---|
| `~/Library/Caches/ms-playwright*` | 重下 100–500MB，且 registry 陈旧不代表用户不再用 | `base.sh:169`、`dev.sh:5855-5858` |
| `~/.m2/repository`、`~/.ivy2/cache`、`~/.sbt/boot`、`~/.sbt/launchers` | 依赖解析目标 / 编译器本体 | `dev.sh:3018-3026` |
| `~/.nuget/packages` | 还原目标本身 | `dev.sh:4126-4130` |
| `~/.pub-cache` | 同上 | `dev.sh:4131` |
| `~/.cache/flutter` | — | `dev.sh:2995` |
| `~/.cargo/registry/src` | 离线构建 + rust-analyzer | `dev.sh:1262-1272` |
| `~/.ollama/models` | 默认白名单 | `base.sh:172` |
| `~/.stack/programs`、`~/.cabal/packages` | 工具链/依赖状态 | `dev.sh:5886-5889` |
| `~/.mix/archives` | 已安装 Mix 工具 | `dev.sh:5882` |
| `~/.cpan/sources` | 发行包存储 | `dev.sh:1305-1306` |
| `~/Library/Caches/CocoaPods/*` | 注释掉 | `dev.sh:2994` |
| `~/.codex`、`~/.claude` | 持久状态 | `dev.sh:5255-5260`、`5858-5861` |
| Homebrew `api/`、`bootsnap/`、`locks/` | 重下 JSON / 重编译 Ruby / 删锁会让 `brew fetch` 失败 | `dev.sh:5966-5976` |
| Xcode `Archives` | 归档产物 | `user.sh:3085` |

---

## 2. 安全闸门（删构建产物前 Mole 到底检查什么）

这是整份文档最重要的一节。Python 实现请逐条对齐。

### 2.1 锁文件？

**源码中不存在任何锁文件检查。** `grep -rn 'package-lock\|yarn.lock\|pnpm-lock\|Cargo.lock\|poetry.lock\|Gemfile.lock\|go.sum'` 在 `lib/` 下零命中。Mole 用的是「Git 跟踪」而不是「锁文件存在」来判断可重建性。

唯一的「锁」是**运行时锁**：Codex daemon releases 删除时用 `lockf` 非阻塞持有 `install.lock`（`dev.sh:4757-4760`、`4833-4884`）。

### 2.2 Git 检查（这是主闸门）

| 检查 | 命令 / 实现 | 位置 |
|---|---|---|
| 找所属仓库 | 从候选路径向上找最近含 `.git`（目录或文件，兼容 worktree/submodule）的祖先 | `purge_shared.sh:170-181` |
| 列被跟踪文件 | `env -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE -u GIT_COMMON_DIR -u GIT_LITERAL_PATHSPECS -u GIT_GLOB_PATHSPECS -u GIT_NOGLOB_PATHSPECS -u GIT_ICASE_PATHSPECS GIT_OPTIONAL_LOCKS=0 git -c core.fsmonitor=false --git-dir=<repo>/.git --work-tree=<repo> -C <repo> ls-files -- <pathspec>` | `purge_shared.sh:193-205` |
| pathspec 构造 | `:(top,literal,icase)<相对路径>`（literal 防通配、icase 兼容 APFS 大小写） | `purge_shared.sh:214-220` |
| 三态返回 | 0=Git 跟踪了文件（保护）；1=无仓库或不跟踪（放行）；2=超时/失败（保护） | `purge_shared.sh:227-243` |

**关键点**：
- 只查「是否被跟踪」，**不查 `git status` / 未提交改动 / stash**。`grep -rn 'git status\|git diff\|--porcelain'` 在 `lib/` 下只有一处注释文字（`caches.sh:682`），无实际调用。
- 但 `mole_path_has_git_tracked_files` 会先 `cd -P` 取物理路径，再找仓库祖先（`purge_shared.sh:234`），因为配置的根可能跨符号链接。
- `clean_project_caches` 的 Git 索引是按仓库批量建的（`caches.sh:490-...`），候选记为 `T`（tracked）/`C`（clear）/`U`（unknown），未记录一律当 unknown→保护（`caches.sh:638-655`）。

### 2.3 作者内容检查（比 Git 更严）

`purge_artifact_has_authored_content`（`project.sh:506-529`）：

```bash
find "$path" \( -name .git -o -name '*-keypair.json' \) -print -quit
```
- 命中 `.git`（嵌套仓库）或 `*-keypair.json`（Solana/Anchor 密钥）→ **保护**；
- 未命中 → 再跑 §2.2 的 Git 检查；
- 任一探测超时/失败 → 返回 2，`is_protected_purge_artifact` 把 `PURGE_PROTECTION_UNVERIFIED=true` 并**保护**（`project.sh:534-560`）。

`is_protected_purge_artifact` 再叠三条按名字的特判（`project.sh:534-570`）：
- `bin`：只有 `is_dotnet_bin_dir` 成立才放行；
- `vendor`：走 §1.11 的 PHP/Rails/Go 判定；
- `DerivedData`：路径含 `/Library/Developer/Xcode/DerivedData` → 保护。

### 2.4 最近修改时间

`classify_purge_activity`（`project.sh:1038-1112`），`MIN_AGE_DAYS = 7`：

```
mtime 读不到            → uncertain（保护）
age < 7 天              → recent（保护）
不是目录                → old（放行）
目录：find <path> -type f -mtime -7 -print -quit
   找到任一近 7 天文件   → recent（保护）
   没找到（完整扫完）    → old（放行）
   find 超时/失败        → uncertain（保护）
```

**重要**：判的是「目录里有没有近 7 天被改的文件」，不是目录自己的 mtime。目录 mtime 只在增删改名时变，靠它会误判。

删除前会再判一次（`purge_target_activity_still_safe`，`project.sh:1122-1140`）：菜单打开时是 old、确认时可能已经活跃；用户显式勾选的 recent 行视为已覆盖提示，但 **uncertain 永远不放行**。

### 2.5 进程占用

| 对象 | 探针 | 位置 |
|---|---|---|
| Gradle | `pgrep -f "org.gradle.launcher.daemon"` / `-f "GradleDaemon"` | `dev.sh:334-341` |
| pnpm | `pgrep -f '(^\|/)pnpm(\.cjs)?([[:space:]]\|$)'` | `dev.sh:343-359` |
| Go mod | `pgrep -x go` / `-x gopls` | `dev.sh:862-869` |
| Rust | `pgrep -x cargo\|rustc\|rustdoc\|clippy-driver\|cargo-nextest` | `dev.sh:1171-1178` |
| Xcode | `pgrep -x Xcode\|xcodebuild\|xctest\|XCTRunner\|XCBBuildService\|swift-frontend` | `app_protection.sh:35-48` |
| Simulator | 上面再加 `Simulator\|simctl`，**且** `simctl list devices booted -j` 无 `"udid"` | `dev.sh:1602-1680` |
| PyInstaller | `pgrep -x pyinstaller` / `-f "[p]yinstaller"` / `-f "[P]yInstaller"` | `dev.sh:755-760` |
| Clang | Xcode 探针 + `pgrep -x clang\|clangd\|swiftc\|sourcekit-lsp\|SourceKitService` | `dev.sh:785-795` |
| GitHub CLI | `pgrep -x gh` | `dev.sh:115` |
| 各 GUI app | 见 §4 的 per-app 探针表 | `user.sh:702-752` |

`mole_pgrep_any`（`base.sh:72-94`）的语义：**任一命中即 0；全部 exit 1 才 1；任一异常即 2**。

### 2.6 是否在项目根

`is_safe_project_artifact_under_root`（`project.sh:344-361`）：
- 路径与搜索根都必须是绝对路径，根不能是 `/`；
- 路径必须以 `<root>/` 开头；
- 深度 < 1（即路径就是根本身）时，只有根自己也是项目根（`is_purge_project_root`）才放行。

`is_safe_project_artifact`（`project.sh:363-405`）在此之上再要求**物理包含**：两边都 `cd -P` 后比较，防「词法前缀在根内、物理上跨符号链接跑到外面」。

`is_safe_configured_purge_artifact`（`project.sh:406-431`）在删除前重新对**所有**配置根验证一遍；`project.sh:409` 先排除 `/` 与 `$HOME`。

### 2.7 符号链接处理

| 层 | 行为 | 位置 |
|---|---|---|
| 扫描 | `find -P`（不跟随）；`fd` 默认不跟随 | `project.sh:580`、`700-760` |
| 容器/缓存根 | `[[ -d x && ! -L x ]]` 才继续；`cd -P` 取物理路径并要求 `physical == lexical` 才算「未被重定向」 | `dev.sh:609-642`、`caches.sh:56-64` |
| 删除前 | `validate_path_for_deletion` 对叶子是链接时，解析目标并检查是否落在关键系统路径 | `file_ops.sh:1173-1196` |
| 删除前 | **祖先链接守卫**：逐级 `[[ -L ]]` 检查父目录；发现链接就 `cd -P` 规范化后重跑 deny 谓词 | `file_ops.sh:1198-1245` |
| 链接本身 | `safe_remove` 走 `safe_remove_symlink`（`rm` 而非 `rm -rf`，只删链接） | `file_ops.sh:2606-2607`、`1840-1925` |
| 缓存叶子 | 多数循环显式 `[[ ! -L "$candidate" ]] \|\| continue` / `[[ -L "$item" ]] && continue` | `dev.sh:771`、`dev.sh:812`、`user.sh:1290`、`user.sh:1365` |

### 2.8 「有未提交改动」检查？

**不存在。** 见 §2.2。Mole 的替代方案是「作者内容探测」（`.git` 目录 + `*-keypair.json` + Git 跟踪），语义是「这里有人的东西」，而不是「有 dirty 文件」。

### 2.9 打开文件句柄（lsof）

`_mole_paths_have_open_handle`（`file_ops.sh:760-798`）：
- 先 `_mole_complete_lsof_mode` 确认能看见 root 进程（`lsof -F pu -p 1` 或 `sudo -n lsof -F pu -p 1`），否则返回 2；
- 再 `lsof -F n -- <paths...>`；
- **exit 1 且 stderr 为空** 才算「确认无句柄」；有任何诊断信息 → 2；
- 注意 `MO_DEBUG=0` 是刻意的（`file_ops.sh:773-777`：debug 输出会污染缓冲区，让 25 个真实可清库里 10 个变成「不知道」）。

SQLite 家族（`file_ops.sh:619-641`）：`<db>`、`<db>-wal`、`<db>-shm` 一次性全部丢给 lsof。

容器缓存用递归版 `_mole_container_cache_has_open_handle`（`file_ops.sh:806-870`）：`lsof -F pfn +D <path>`。

### 2.10 删除边界的三元组身份校验

`safe_remove`（`file_ops.sh:1451-1838`）在 `rm -rf` 前：
1. 重跑 `validate_path_for_deletion`；
2. 重跑 `is_path_whitelisted`；
3. 重跑 `holds_compiled_model_cache`；
4. 若 caller 通过 `_MOLE_SAFE_CLEAN_BOUND_PATH` 绑定了 `parent:inode` + `target:inode`，用 `_mole_path_matches_identity` 复核；
5. `_mole_owned_path_still_valid`（`file_ops.sh:2301`）核对 dev/inode/mtime/sha256。

这套是防「扫描时是 A，删除时路径已被换成 B」。

### 2.11 关键系统路径黑名单

`_mole_is_critical_deletion_path`（`file_ops.sh:1022-1111`）拒绝：

```
/ /bin /dev /sbin /usr /System /Library /Library/Apple /Library/Extensions
/Library/Keychains /Applications /Volumes /opt /opt/homebrew
/Users /Users/Shared /Users/Guest
/etc /private/etc /var /var/db /var/audit /var/root
/private /private/tmp /private/var /private/var/tmp /private/var/folders
/private/var/db /private/var/audit /private/var/root
/Applications/Finder.app /Applications/Safari.app
```
另外：
- `/Users/<name>`（恰好一级）单独拒绝，`/Users/<name>/...` 允许；
- 用 `_mole_path_is_same_existing_file` 再比一次大小写变体（`/SYSTEM`、`/OPT/HOMEBREW`）；
- `/usr/local/*` 与 `/opt/homebrew/*` 的**子项**是允许的（`file_ops.sh:1026-1029`）。

### 2.12 段级预算

- 系统段整体 120s（`system.sh:229`）；
- 开发者段每步独立预算，无总预算；
- `clean_project_caches` 每根 6s 扫描 + 发现 2s；
- 超预算不是错误，是「本轮到此为止」，记 `report_system_cleanup_budget_reached`。

---

## 3. 系统缓存规则

全部在 `clean_deep_system`（`system.sh:224-877`），**需要 sudo**，整体预算 120s。

### 3.1 删除项

| # | 路径 | 模式 | 保留期 | maxdepth | 说明 | 来源 |
|---|---|---|---|---|---|---|
| S1 | `/Library/Caches` | `*.cache` | 7 天 | 5 | `System caches` | `system.sh:238-239` |
| S1b | `/Library/Caches` | `*.tmp`（恒有）；`*.log`（仅当 `MOLE_LOG_AGE_DAYS == MOLE_TEMP_FILE_AGE_DAYS`，当前都是 7） | 7 天 | 5 | 同上 | `system.sh:233-236` |
| S1c | `/Library/Caches` | `*.log` | `MOLE_LOG_AGE_DAYS`=7 | 5 | 同上（仅当两个常量不等时单独跑一遍） | `system.sh:253-255` |
| S2 | `/Library/Logs/DiagnosticReports` | `*` | 7 天 | **1** | `System crash reports` | `system.sh:281-282` |
| S3 | `/private/var/log` | `*.log`、`*.gz`、`*.asl` | 7 天 | 3 | `System logs` | `system.sh:303-304` |
| S4 | `/Library/Logs/Adobe`、`/Library/Logs/CreativeCloud` | `*` | 7 天 | 5 | `Third-party system logs` | `system.sh:326-340` |
| S5 | `/Library/Logs/adobegc.log` | 精确名 | 7 天 | 1 | 同上 | `system.sh:358` |
| S6 | `/Library/Caches/com.apple.iconservices.store` | 整目录 | 无 | — | `Rebuildable system caches` | `system.sh:540-566` |
| S7 | `/private/var/folders/*/*/C/*/{com.apple.gpuarchiver,com.apple.metal,com.apple.metalfe}` | 整目录 | **1 天**（目录内无近 1 天被改文件） | 扫描 maxdepth 8 | `Accessible rebuildable GPU caches` | `system.sh:5-52`、`591-640` |
| S8 | `<root>/private/var/folders` 下 `com.apple.idleassetsd` 的 `CFNetworkDownload_*.tmp` | 文件名 | 7 天 | 5 | `Stale wallpaper downloads` | `system.sh:673-704` |
| S9 | `/private/var/db/diagnostics` | `*` | 7 天 | 5 | `System diagnostic logs` | `system.sh:736-741` |
| S10 | `/private/var/db/DiagnosticPipeline` | `*` | 7 天 | 5 | 同上 | `system.sh:758` |
| S11 | `/private/var/db/powerlog` | `*` | 7 天 | 5 | `Power logs` | `system.sh:784` |
| S12 | `/private/var/db/reportmemoryexception/MemoryLimitViolations` | `*` | **30 天** | 5 | `Memory exception reports` | `system.sh:808-843` |
| S13 | `/Applications/Install macOS*.app` | 整包 | **14 天** + 4 条附加条件 | — | `<app>, <size>` | `system.sh:391-505` |
| S14 | `<DARWIN_USER_DIR>/../X/<bundle>.code_sign_clone/code_sign_clone.*` | 整目录 | 无（需无 Chrome/Chromium 进程） | — | `Browser code signature caches` | `browser_clones.sh:4-11`、`89-117` |

S13 的六条附加条件（`macos_installer_candidate_still_eligible`，`system.sh:170-222`）：
1. 目录存在且非链接；
2. `stat -f%d:%i:%m` 三元组与调用者记录的一致（防替换）；
3. mtime ≥ 14 天前；
4. `software_update_pending_or_unknown` 判定**没有**待装更新（读 `/Library/Preferences/com.apple.SoftwareUpdate.plist`，读不到也算 pending → 保护）；
5. `pgrep -f <path>` 无进程；
6. `Contents/Info.plist` 的 `DTPlatformVersion` 主版本号 ≠ 当前 `sw_vers -productVersion` 主版本号（防删掉当前系统的恢复安装器）。

### 3.2 明确保留项

| 路径 | 理由 | 来源 |
|---|---|---|
| `/Library/Updates` | Software Update 拥有，scan→delete 窗口内无法证明不活跃 | `system.sh:381-386` |
| `/macOS Install Data` | 同上 | `system.sh:387-389` |
| `/private/tmp`、`/private/var/tmp` 内容 | 年龄 + 有界扫描不能证明第三方运行时状态可弃 | `system.sh:270-273` |
| `/System/Library/Caches/*` | 除 `com.apple.coresymbolicationd/data`（`file_ops.sh:1247-1252` 白名单）外全在黑名单 | `file_ops.sh:1022-1111` |
| `com.apple.idleassetsd` 的壁纸视频 | mtime 是下载时间，不代表是否在用 | `user.sh:995-999`、`app_protection.sh:600-606` |
| `com.apple.wallpaper` | 用户选中内容 | `app_protection.sh:600-606` |
| `com.apple.containermanagerd`、`com.apple.homed`、`FamilyCircle`、`HomeKit`、`ap.adprivacyd` | 系统服务状态 | `app_protection.sh:584-597` |
| `GIPPseudonymousID`、`CCTClearcutLogger` | 删了会让 Gemini 进入无界写入循环 | `app_protection.sh:598-602` |
| `com.apple.coreaudio*`、`coreaudiod*` | 会让 Intel Mac 丢音频输出 | `app_protection.sh:607-610` |
| `ms-playwright`、`app.cotypist.Cotypist`、`com.displaylink.DisplayLinkUserAgent`、`com.lasersoft-imaging.*`、`Adobe *` | 重下/重装成本高 | `app_protection.sh:578-583` |
| 音频插件（VST3、iZotope、LaserSoft、Native Instruments、Avid、FabFilter、PACE） | 授权/插件本体 | `app_protection.sh:558-576` |

### 3.3 Time Machine / 快照

`clean_time_machine_failed_backups`（`system.sh:934-1360`）：
1. 无 `tmutil` → 直接返回；
2. `defaults read /Library/Preferences/com.apple.TimeMachine AutoBackup` 必须是 `0` 或 `1`，否则返回；
3. `tmutil destinationinfo` 无目标 → 返回；
4. `tm_is_running`（`system.sh:1362-1385`）三态：0 运行 / 1 空闲 / 2 未知。**运行或未知都跳过**；
5. 只在 `/Volumes/*` 里找同时含 `Backups.backupdb` 或 `.MobileBackups` 的卷，跳过 `/` 与 `MacintoshHD`；
6. 只删**未完成**的备份，且年龄 > `MOLE_TM_BACKUP_SAFE_HOURS`=48 小时（`system.sh:1099`、`1253`）。

`clean_local_snapshots`（`system.sh:1387-1435`）：**只报告不删**，输出 `Time Machine local snapshots · N (review: tmutil listlocalsnapshots /)`。

---

## 4. 应用缓存规则

### 4.1 四个通用机制（先理解这些，再看表）

**(a) `~/Library/Caches/*` 整体清扫**（`clean_user_essentials`，`user.sh:248-303`）
- 枚举 `~/Library/Caches` 下每个一级子项，**排除 DENO_DIR 及其物理路径**；
- 一次性丢给 `safe_clean_guarded _user_cache_deno_delete_guard ... "User app cache"`；
- 所以「纯缓存」和「用户数据」的分界，最终由 `should_protect_path` + 默认/安全白名单决定，而不是由这条 glob 决定。

**(b) 沙盒容器扫描**（`process_container_cache`，`user.sh:1330-1420`）
- 遍历 `~/Library/Containers/*`，要求存在 `Data/Library/Caches`；
- 跳过 `is_critical_system_component(bundle_id)` 与 `should_protect_data(bundle_id)`；
- 逐个一级子项，跳过 `holds_compiled_model_cache` / `should_protect_path` / `is_path_whitelisted`；
- 一级子项 > 100 时不再逐项 du，改批量删（`user.sh:1349-1356`）。

**(c) Group Containers 扫描**（`clean_group_container_caches`，`user.sh:1428-...`）
- 跳过 `com.apple.*`、`group.com.apple.*`、`systemgroup.com.apple.*`（Apple 共享容器整个不碰）；
- 跳过 Safari Web Extension 容器（清缓存会触发扩展重初始化并可能拉起 Safari，`user.sh:1461-1475`）；
- 候选：`Logs`、`Library/Logs` 恒有；`tmp`、`Library/tmp`、`Caches`、`Library/Caches` 仅在容器非受保护时才有。

**(d) Application Support 里 Electron 风格缓存**（`clean_application_support_logs`，`user.sh:2476-...`）
- 只在**目录里已有可重建缓存标记**时才把 `Cache` / `CachedData` 加进候选（`user.sh:2536-2553`）；
- 固定候选：`Cache`、`GPUCache`、`DawnCache`、`GrShaderCache`、`GraphiteDawnCache`、`DawnGraphiteCache`、`DawnWebGPUCache`、`Crashpad/completed`。

### 4.2 浏览器

| 浏览器 | 删 | 进程守卫 | 来源 |
|---|---|---|---|
| Safari | `~/Library/Caches/com.apple.Safari/*` | 无 | `user.sh:1963` |
| Chrome | `~/Library/Caches/Google/Chrome/*`；`.../Application Support/Google/Chrome/*/{Application Cache,Code Cache,GPUCache,DawnCache,GrShaderCache,GraphiteDawnCache}/*`；`.../Chrome/{component_crx_cache,ShaderCache,GrShaderCache,GraphiteDawnCache,Crashpad/completed,OptGuideOnDeviceModel,OptGuideOnDeviceClassifierModel,optimization_guide_model_store}/*` | `pgrep -x "Google Chrome"` | `user.sh:1965`、`754-783` |
| Chrome Service Worker | `<profile>/Service Worker/CacheStorage/<origin-hash>/*`（`-mindepth 2 -maxdepth 2`） | 同上 | `caches.sh:49-205` |
| Chromium | `~/Library/Caches/Chromium/*` | 无 | `user.sh:2007` |
| Edge | `~/Library/Caches/com.microsoft.edgemac/*` | 无 | `user.sh:2009` |
| Arc | `~/Library/Caches/company.thebrowser.Browser/*`；`Application Support/Arc/{*/Code Cache,*/GPUCache,*/DawnCache,*/GrShaderCache,*/GraphiteDawnCache,ShaderCache,GrShaderCache,GraphiteDawnCache,Crashpad/completed,User Data/*/…,User Data/{component_crx_cache,extensions_crx_cache,Crashpad/completed}}` | `pgrep -x Arc` | `user.sh:2012-2045` |
| Dia | `~/Library/Caches/company.thebrowser.dia/*`；`~/Library/Caches/Dia/User Data/*/{Cache,Code Cache}/*`；`Application Support/Dia/User Data/{GraphiteDawnCache,GPUPersistentCache,component_crx_cache,extensions_crx_cache}/*`、`.../User Data/*/{DawnGraphiteCache,DawnWebGPUCache,GPUCache}/*` | `pgrep -x Dia` | `user.sh:2047-2078` |
| Brave | `~/Library/Caches/BraveSoftware/Brave-Browser/*`；`Application Support/BraveSoftware/Brave-Browser/*/{Application Cache,Code Cache,GPUCache,DawnCache,…}/*` | `pgrep -x Brave Browser` | `user.sh:2084-…` |
| Firefox | `~/Library/Caches/Firefox/*`；`Application Support/Firefox/Profiles/*/cache2/*` | `_firefox_process_state` | `user.sh:785-790` |
| Opera | `~/Library/Caches/com.operasoftware.Opera/*` | 无 | `user.sh:2140` |
| Vivaldi | `~/Library/Caches/com.vivaldi.Vivaldi/*`；`Application Support/Vivaldi/*/{Code Cache,GPUCache,DawnCache,GrShaderCache,GraphiteDawnCache}/*` | `pgrep -x Vivaldi` | `user.sh:2143-2152` |
| QQ 浏览器 | `~/Library/Caches/com.tencent.QQBrowser3/*`；`Application Support/QQBrowser3/*/{Code Cache,GPUCache}/*`、`QQBrowser3/{ShaderCache,GrShaderCache,GraphiteDawnCache,component_crx_cache,Crashpad/completed}/*` | `pgrep -x QQBrowser3` | `user.sh:2169-2183` |
| Comet / Orion / Zen | `~/Library/Caches/Comet/*`、`com.kagi.kagimacOS/*`、`zen/*` | 无 | `user.sh:2162-2164` |
| Puppeteer | `~/.cache/puppeteer/*` | 无 | `user.sh:2008` |

**Service Worker 保护域**（`bin/clean.sh:54-77`）—— 命中即跳过：

```python
PROTECTED_SW_DOMAINS = [
    "capcut.com", "photopea.com", "pixlr.com",                       # Web 编辑器
    "docs.google.com", "sheets.google.com", "slides.google.com",
    "drive.google.com", "mail.google.com",                          # Google Workspace 离线
    "github.com", "gitlab.com", "codepen.io", "codesandbox.io",
    "replit.com", "stackblitz.com",                                 # 代码平台 PWA
    "notion.so", "figma.com", "linear.app", "excalidraw.com",       # 协作工具
]
```

**绝不删 `Service Worker/ScriptCache`**（MV3 扩展字节码，删了会让扩展 SW 崩并触发安全告警），见 `user.sh:1966-1969`、`app_caches.sh:563-568`。

### 4.3 聊天 / 会议 / 协作

| 应用 | 删 | 来源 |
|---|---|---|
| Discord | `~/Library/Application Support/discord/Cache/*` | `app_caches.sh:845` |
| Legcord | `~/Library/Application Support/legcord/Cache/*` | `app_caches.sh:846` |
| Slack | `~/Library/Application Support/Slack/Cache/*` | `app_caches.sh:847` |
| Zoom | `~/Library/Caches/us.zoom.xos/*` | `app_caches.sh:848` |
| 微信 | `~/Library/Caches/com.tencent.xinWeChat/*`（旧布局，常不存在）；`~/Library/Containers/com.tencent.xinWeChat/Data/Documents/app_data/log`、`…/.wxapplet/WMPF` | `app_caches.sh:849`、`796-815` |
| 企业微信 | `~/Library/Caches/com.tencent.WeWorkMac/*`；`…/Data/Library/Application Support/WXWork/Log`；`…/Data/Documents/cefcache/<profile>/{Service Worker/CacheStorage,Cache,Code Cache,GPUCache}`（profile 名须为 `Default` 或 `wew_<数字>`） | `app_caches.sh:856`、`816-840` |
| Telegram | `~/Library/Caches/ru.keepcoder.Telegram/*` | `app_caches.sh:850` |
| Teams | `~/Library/Caches/com.microsoft.teams2/*`；`Application Support/Microsoft/Teams/{Cache,Application Cache,Code Cache,GPUCache,logs,tmp}/*` | `app_caches.sh:852`、`862-867` |
| WhatsApp | `~/Library/Caches/net.whatsapp.WhatsApp/*` | `app_caches.sh:853` |
| Skype | `~/Library/Caches/com.skype.skype/*` | `app_caches.sh:854` |
| 腾讯会议 | `~/Library/Caches/com.tencent.meeting/*` | `app_caches.sh:855` |
| QQ | `~/Library/Caches/com.tencent.qq/*` | `app_caches.sh:857` |
| 飞书 / Lark | `~/Library/Caches/com.feishu.*/*`；`Application Support/{LarkShell,LarkInternational}/aha/users/*/profile_explorer/Service Worker/CacheStorage` | `app_caches.sh:858`、`493-548` |
| 钉钉 | `~/Library/Caches/dd.work.exclusive4aliding/*`、`com.alibaba.AliLang.osx/*`；`Application Support/iDingTalk/{log,holmeslogs}/*` | `app_caches.sh:873-877` |
| Notion | `~/Library/Caches/notion.id/*`；`Application Support/Notion/Partitions/*/Service Worker/CacheStorage` | `app_caches.sh:1860`、`579-626` |

**微信/企微的「用户数据」分界**（`app_caches.sh:645-651` 注释，逐条列出）：
```
不删：WeChat  Documents/xwechat_files/**       聊天数据库与收到的文件
       WeChat  Documents/app_data/radium/users 每账号 webview 状态
       WeCom   Documents/Profiles/*/Messages1  聊天消息
       WeCom   Documents/cefcache/<profile>/   cookies / Account Web Data / 登录态
       WeCom   Documents/Profiles/*/Publishsys 分发载荷
```

### 4.4 IDE / 编辑器

| 应用 | 删 | 来源 |
|---|---|---|
| VS Code | `Application Support/Code/{logs,Cache,CachedExtensionVSIXs,CachedData}/*`、`Code/WebStorage/*/CacheStorage/*` | `app_caches.sh:437-442` |
| Cursor | `Application Support/Cursor/CachedExtensionVSIXs/*` | `app_caches.sh:440` |
| 过期扩展 | `~/.vscode/extensions`、`~/.vscode-insiders/extensions`、`~/.cursor/extensions` 里被 `.obsolete` 标记为 true 的一级目录 | `app_caches.sh:410-434` |
| Sublime | `~/Library/Caches/com.sublimetext.*/*` | `app_caches.sh:443` |
| Zed | `~/Library/Caches/Zed/*`、`Application Support/Zed/node/cache/*`、`Application Support/Zed/node/node-v*/cache/*`、`~/Library/Logs/Zed/*` | `app_caches.sh:444-449` |
| CodeBuddy | `Application Support/CodeBuddyExtension/{Cache,logs}/*`；`CodeBuddy CN/{Cache,CachedData,CachedExtensionVSIXs,Code Cache,GPUCache,DawnGraphiteCache,DawnWebGPUCache,logs}/*` | `app_caches.sh:453-465` |
| Qoder | `Application Support/Qoder/{Cache,CachedData,CachedExtensionVSIXs,Code Cache,GPUCache,DawnGraphiteCache,DawnWebGPUCache,logs}/*` | `dev.sh:5822-5829` |
| Antigravity | `Application Support/Antigravity/{Cache,Code Cache,GPUCache,DawnGraphiteCache,DawnWebGPUCache}/*` | `dev.sh:5792-5798` |
| Filo | `Application Support/Filo/production/{Cache,Code Cache,GPUCache,DawnGraphiteCache,DawnWebGPUCache}/*` | `dev.sh:5804-5810` |
| mihomo-party | `Application Support/mihomo-party/{Cache,Code Cache,GPUCache,DawnGraphiteCache,DawnWebGPUCache,logs}/*` | `app_caches.sh:1848-1853` |
| JetBrains Toolbox | `Application Support/JetBrains/Toolbox/apps/<product>/<ch-*>/<版本号>` 按 mtime 降序，保留最新 1 个（`MOLE_JETBRAINS_TOOLBOX_KEEP`），跳过 `current`、`plugins`、`plugins-lib(s)`、非数字开头 | `dev.sh:3141-3246` |
| JetBrains IDE | `~/Library/Logs/JetBrains/*` | `dev.sh:3249` |
| JetBrains 缓存 | `~/Library/Caches/JetBrains*`、`com.jetbrains.toolbox*` **默认白名单** | `base.sh:176-177` |

`.obsolete` 解析（`app_caches.sh:376-407`）刻意不用 `plutil -p`：其输出格式不稳定（macOS 15 把 `true` 印成 `1`），且不带嵌套信息。改用 `plutil -convert xml1` 后按标签深度判断，只接受根 dict 下值恰为 `<true/>` 的键。

### 4.5 设计 / 3D / 视频

| 应用 | 删 | 来源 |
|---|---|---|
| Sketch | `~/Library/Caches/com.bohemiancoding.sketch3/*`、`Application Support/com.bohemiancoding.sketch3/cache/*` | `app_caches.sh:897-898` |
| Adobe 全家桶 | `~/Library/Caches/Adobe/*`、`~/Library/Caches/com.adobe.*/*`、`Application Support/Adobe/Common/Media Cache Files/*`、`~/Library/Caches/com.adobe.PremierePro.*/*` | `app_caches.sh:899-902`、`968` |
| Figma | `~/Library/Caches/com.figma.Desktop/*` | `app_caches.sh:901`、`dev.sh:5787` |
| Blender | `~/Library/Caches/org.blenderfoundation.blender/*` | `app_caches.sh:1582` |
| Cinema 4D | `~/Library/Caches/com.maxon.cinema4d/*` | `app_caches.sh:1583` |
| SketchUp | `~/Library/Caches/com.sketchup.*/*` | `app_caches.sh:1614` |
| Autodesk | `~/Library/Caches/com.autodesk.*`（逐个 entry，需无 AcCoreConsole/ADPClientService 等 helper） | `app_caches.sh:1587-1612` |
| Fusion 旧版本 | `Application Support/Autodesk/webdeploy/production/<旧版本>`（保留 current 与更新的 staged） | `app_caches.sh:1394-1580` |
| Final Cut | `~/Library/Caches/com.apple.FinalCut/*` | `app_caches.sh:965` |
| DaVinci | `~/Library/Caches/com.blackmagic-design.DaVinciResolve/*`、`~/Movies/CacheClip/*` | `app_caches.sh:966-967` |
| ScreenFlow | `~/Library/Caches/net.telestream.screenflow10/*` | `app_caches.sh:964` |
| 剪映 | `~/Movies/JianyingPro/User Data/Cache/{recognize,frameThumbnail,audioWave,AlgorithmCache,ILASDKDB,ttsTemp,tmp}` | `app_caches.sh:928-962` |

**剪映的分界**（`app_caches.sh:943-946`）：只清 scratch 子目录；渲染/转码后的成片可能是原始素材丢失后的唯一副本，导入文件、下载素材、工程数据全部不碰。

**Autodesk 的风险说明**（`app_caches.sh:971-976`）：helper 进程会活过主窗口并持续持有 SQLite 缓存，此时删会「用未链接的临时写入把卷填满」。

### 4.6 音乐 / 视频播放器 / 下载器

| 应用 | 删 | 来源 |
|---|---|---|
| Apple Music | `~/Library/Caches/com.apple.Music`（整目录） | `app_caches.sh:1641` |
| Apple Podcasts | `~/Library/Caches/com.apple.podcasts`；`Containers/com.apple.podcasts/Data/tmp/{StreamedMedia,*.heic,*.img,*CFNetworkDownload*.tmp}` | `app_caches.sh:1642-1647` |
| Apple TV | `~/Library/Caches/com.apple.TV/*` | `app_caches.sh:1648` |
| Plex | `~/Library/Caches/tv.plex.player.desktop` | `app_caches.sh:1649` |
| 网易云 | `~/Library/Caches/com.netease.163music` | `app_caches.sh:1650` |
| QQ 音乐 | `~/Library/Caches/com.tencent.QQMusic/*`、`com.tencent.QQMusicMac/*`、`Containers/com.tencent.QQMusicMac/Data/Library/Caches/*`、`…/Application Support/QQMusicMac/{iRRCache,iLog,iCache,iTemp}/*` | `app_caches.sh:1651-1661` |
| 酷狗 / 酷我 | `~/Library/Caches/com.kugou.mac/*`、`com.kuwo.mac/*` | `app_caches.sh:1662-1663` |
| **Spotify** | **不删**（缓存里可能有离线音乐，缺 marker 不代表可弃） | `app_caches.sh:1639-1640` |
| IINA / VLC / MPV | `~/Library/Caches/{com.colliderli.iina,org.videolan.vlc,io.mpv}` | `app_caches.sh:1667-1669` |
| 爱奇艺 / 腾讯视频 | `~/Library/Caches/{com.iqiyi.player,com.tencent.tenvideo}`；`Containers/com.tencent.tenvideo/Data/Library/Application Support/{Upgrade,VideoNative,documentCache}/*` | `app_caches.sh:1670-1678` |
| B站 / 斗鱼 / 虎牙 | `~/Library/Caches/{tv.danmaku.bili/*,com.douyu.*/*,com.huya.*/*}` | `app_caches.sh:1679-1681` |
| Stremio | `~/Library/Caches/smart.stremio*/*`、`Application Support/stremio/stremio-server/stremio-cache/*` | `app_caches.sh:1683-1686` |
| Aria2 / Transmission / qBittorrent / Downie / Folx / Pacifist | `~/Library/Caches/{net.xmac.aria2gui,org.m0k.transmission,com.qbittorrent.qBittorrent,com.downie.Downie-*,com.folx.*/*,com.charlessoft.pacifist/*}` | `app_caches.sh:1690-1695` |
| Neat Download Manager | `Application Support/com.NeatDownloadManager/<数字>/`（名字纯数字、含 `seg.x0`、`seg.x0` 年龄 ≥ 30 天）；**`NeatDB.db` 永不碰** | `app_caches.sh:1702-1762` |

### 4.7 网盘 / 云同步

| 应用 | 删 | 进程守卫 | 来源 |
|---|---|---|---|
| Dropbox | `~/Library/Caches/com.dropbox.*`、`~/Library/Caches/com.getdropbox.dropbox` | `pgrep -x "Dropbox"` | `user.sh:723`、`792-797`、`2197` |
| Google Drive | `~/Library/Caches/com.google.GoogleDrive` | `pgrep -x "Google Drive"` | `user.sh:727`、`2216-2219` |
| OneDrive | `~/Library/Caches/com.microsoft.OneDrive` | `pgrep -x "OneDrive"` | `user.sh:731`、`2236-2239` |
| 百度网盘 | `~/Library/Caches/com.baidu.netdisk` | 无 | `user.sh:2224` |
| 阿里云盘 | `~/Library/Caches/com.alibaba.teambitiondisk` | 无 | `user.sh:2225` |
| Box | `~/Library/Caches/com.box.desktop` | 无 | `user.sh:2226` |

**`~/Library/CloudStorage` 与 `~/Library/Mobile Documents`**：整体属于默认白名单（`base.sh:181`），且 purge 扫描时用 `-flags +dataless` 跳过在线占位目录（`project.sh:816-830`），避免遍历触发网络下载把扫描预算耗尽。

### 4.8 笔记 / 效率 / 工具 / 其他

```
notion.id, md.obsidian, com.logseq.*, com.bear-writer.*, com.evernote.*, com.yinxiang.*   app_caches.sh:1860-1865
com.tw93.MiaoYan, com.klee.desktop, klee_desktop, com.orabrowser.app,
com.filo.client, com.flomoapp.mac                                                    app_caches.sh:1624-1629
com.runningwithcrayons.Alfred, cx.c3.theunarchiver                                   app_caches.sh:1869-1870
com.teamviewer.*, com.anydesk.*, com.todesk.*, com.sunlogin.*                        app_caches.sh:1874-1877
com.youdao.YoudaoDict, com.eudic.*, com.bob-build.Bob                                app_caches.sh:1806-1808
com.cleanshot.*, com.reincubate.camo, com.xnipapp.xnip                               app_caches.sh:1812-1814
com.readdle.smartemail-Mac, com.airmail.*                                            app_caches.sh:1818-1819
com.todoist.mac.Todoist, com.any.do.*                                                app_caches.sh:1823-1824
com.runjuu.Input-Source-Pro, macos-wakatime.WakaTime                                 app_caches.sh:1841-1842
ws.stash.app.mac, com.mitchellh.ghostty, dev.warp.Warp-Stable                        app_caches.sh:1834-1856
~/Library/Application Support/WeType/{com.onevcat.Kingfisher.ImageCache.WeType,DictUpdate}/*  app_caches.sh:1844-1845
~/.zcompdump*, ~/.lesshst, ~/.viminfo.tmp, ~/.wget-hsts, ~/.cacher/logs/*, ~/.kite/logs/*    app_caches.sh:1828-1833
~/Library/Logs/warp.log, ~/Library/Caches/SentryCrash/Warp/*                         app_caches.sh:1835-1836
```

游戏平台（`app_caches.sh:1764-1804`）：Steam `~/Library/Caches/com.valvesoftware.steam/*` + `Application Support/Steam/{htmlcache,appcache,depotcache,steamapps/shadercache,logs}/*`；Epic / Battle.net / EA / GOG / Riot；Minecraft `{logs,crash-reports,webcache,webcache2}/*`；Lunar Client `{game-cache,launcher-cache,logs}/*`；PCSX2 / RPCS3。

### 4.9 系统级 Apple 缓存（`clean_app_caches`，`user.sh:1162-1275`）

```
~/Library/Saved Application State/*                  Saved application states          user.sh:1169
~/Library/Caches/com.apple.photoanalysisd            Photo analysis cache              user.sh:1170
~/Library/Caches/com.apple.akd                       Apple ID cache                    user.sh:1171
~/Library/Caches/com.apple.WebKit.Networking/*       WebKit network cache              user.sh:1172
~/Library/DiagnosticReports/*                        Diagnostic reports                user.sh:1173
~/Library/Caches/com.apple.QuickLook.thumbnailcache  QuickLook thumbnails              user.sh:1174
~/Library/Caches/Quick Look/*                        QuickLook cache                   user.sh:1175
~/Library/Caches/com.apple.iconservices*             Icon services cache               user.sh:1176
~/Library/IdentityCaches/*                           Identity caches                   user.sh:1180
~/Library/Suggestions/*                              Siri suggestions cache            user.sh:1181
~/Library/Application Support/AddressBook/Sources/*/Photos.cache  Address Book photo cache  user.sh:1186
~/Library/Caches/com.apple.helpd/*                   macOS Help system cache           user.sh:1206
~/Library/Caches/GeoServices/*                       Maps geo tile cache               user.sh:1207
~/Library/Caches/com.apple.{AppleMediaServices,duetexpertd,parsecd,python}/*            user.sh:1215-1218
~/Library/Messages/{StickerCache,Caches/Previews/Attachments,Caches/Previews/StickerCache}/*  user.sh:1002-1004
~/Library/Containers/<id>/Data/Library/Caches/*      Sandboxed app caches              user.sh:1194-1214
~/Library/Group Containers/<id>/{Logs,Library/Logs,tmp,Library/tmp,Caches,Library/Caches}/*  user.sh:1428+
~/Library/Group Containers/group.com.apple.coreservices.useractivityd/shared-pasteboard/*  user.sh:1277-1327
~/Library/Apple/usr/share/rosetta/rosetta_update_bundle   Rosetta 2 cache (仅 Apple Silicon)  user.sh:3601
~/Library/Caches/com.apple.rosetta.update                 Rosetta 2 user cache        user.sh:3602
```

**Handoff 剪贴板**（`user.sh:1277-1327`）：`find ... -mindepth 1 -maxdepth 1 -mmin +60`，即只删 **60 分钟前**的条目，防打断跨设备剪贴板同步。

**明确不删**（`user.sh:1177-1185` 注释）：
- `~/Library/Autosave Information`（可能含可恢复文档）；
- `~/Library/Calendars/Calendar Cache*`（CalendarAgent 后台持有 SQLite，删了会让 Calendar.app 崩到重新登录，issue #1508）；
- `~/Library/Application Support/com.apple.wallpaper/aerials/` 缩略图（约 50KB 的封面预览，删了全变云下载占位符，issue #1118）。

### 4.10 用户日志与崩溃报告

`_clean_user_log_directory`（`user.sh:205-246`）：

| 调用 | 根 | 行为 |
|---|---|---|
| `user.sh:307` | `~/Library/Logs` | 删**所有**一级子项（**无年龄过滤**）；`DiagnosticReports` 目录本身保留，删其**子项** |
| `user.sh:1173` | `~/Library/DiagnosticReports` | 删所有一级子项（旧版 macOS 路径） |

`~/Library/Application Support/CrashReporter/*` 走 30 天过滤（`clean_support_app_data`，`user.sh:987-993`，`support_age_days=30`）。

`~/Library/Logs/DiagnosticReports` 的**目录本身**永远保留（`user.sh:224-231`，issue #1689），包括它被换成链接的情况。

---

## 5. 优化任务目录（optimize）

注册表在 `catalog.sh:31-85`，19 个任务全部 `safe=true`。所有任务按同一套 outcome 报告：`applied` / `unchanged` / `skipped` / `unavailable` / `attention` / `failed`（`outcomes.sh:18-23`）。

| # | action | 说明文案 | 命令 | 风险 | 位置 |
|---|---|---|---|---|---|
| O1 | `cache_refresh` | Refresh QuickLook thumbnails & icon services cache | `qlmanage -r cache`；`qlmanage -r`；然后 `safe_remove` 三个目录 | 低（自动重建） | `tasks.sh:189-290` |
| O2 | `saved_state_cleanup` | Remove old saved application states (30+ days) | `find ~/Library/Saved\ Application\ State -type d -name "*.savedState" -mtime +30` → `safe_remove` | 低 | `tasks.sh:296-345` |
| O3 | `fix_broken_configs` | Fix corrupted preferences files | `plutil -lint` 批量 lint `~/Library/Preferences`（maxdepth 1，保护 `loginwindow.plist`）与 `ByHost`（递归）；坏文件删除 | 低（会重建） | `tasks.sh:361-400`、`maintenance.sh:112-129` |
| O4 | `network_optimization` | Refresh DNS cache | `sudo dscacheutil -flushcache && sudo killall -HUP mDNSResponder` | 低；**有活动 VPN 时跳过** | `tasks.sh:165-187`、`404-437` |
| O5 | `sqlite_vacuum` | Compress SQLite databases for Mail, Safari & Messages (skips if apps are running) | `sqlite3 <db> "VACUUM;"` | 低；多重前置检查 | `tasks.sh:504-694` |
| O6 | `prevent_network_dsstore` | 关闭网络/USB 卷上的 .DS_Store | `defaults write com.apple.desktopservices DSDontWriteNetworkStores -bool true`、`DSDontWriteUSBStores -bool true` | 极低，可逆 | `tasks.sh:1066-1113` |
| O7 | `legacy_overrides_audit` | 清理旧 tweak 工具留下的隐藏覆盖 | `defaults read -g NSAppSleepDisabled`；`defaults read com.apple.frameworks.diskimages skip-verify{,-locked,-remote}`；命中则 `defaults delete` | 低 | `tasks.sh:1115-1200` |
| O8 | `network_stack_optimize` | 刷路由表与 ARP | `sudo route -n flush`；`sudo arp -a -d` | 中；**有活动 VPN 时跳过**；先做健康检查，两项都正常就不动 | `tasks.sh:696-799` |
| O9 | `disk_permissions_repair` | 修用户目录权限 | `sudo diskutil resetUserPermissions / <uid>` | **中**（需 sudo，改权限） | `tasks.sh:801-853` |
| O10 | `spotlight_index_optimize` | 搜索慢时重建 Spotlight 索引 | `sudo mdutil -E /` | 中（索引 1–2 小时） | `tasks.sh:855-958` |
| O11 | `spotlight_orphan_rules_cleanup` | 清已卸载 app 的 Spotlight 搜索规则 | `defaults write com.apple.spotlight EnabledPreferenceRules -array ...` | 低 | `tasks.sh:960-1064` |
| O12 | `periodic_maintenance` | 跑 macOS 日/周/月维护脚本（若已陈旧） | `sudo periodic daily weekly monthly` | 中（可能长） | `tasks.sh:1264-1313` |
| O13 | `shared_file_list_repair` | 修损坏的 Finder 收藏/最近文档 | `plutil -lint` 扫 `*.sfl2/sfl3/sfl4`，坏则删；**跳过 `*ApplicationRecentDocuments*`** | 低 | `tasks.sh:1315-1397` |
| O14 | `disk_verify` | 校验文件系统 | `diskutil verifyVolume /` | **高；默认关闭**，需 `MOLE_ENABLE_DISK_VERIFY=1` | `tasks.sh:1470-1510` |
| O15 | `login_items_audit` | 审计失效登录项 | 只报告，不删 | 无 | `tasks.sh:1923-...` |
| O16 | `quarantine_cleanup` | 清 Gatekeeper 下载历史 | `sqlite3 ~/Library/Preferences/com.apple.LaunchServices.QuarantineEventsV2 "DELETE FROM LSQuarantineEvent; VACUUM;"` | 低 | `tasks.sh:441-502` |
| O17 | `launch_agents_cleanup` | 报告程序已不存在的 LaunchAgent | `PlistBuddy -c "Print :Program"`（回退 `:ProgramArguments:0`）；**只报告** | 无 | `tasks.sh:1202-1262` |
| O18 | `notification_cleanup` | 清 30 天前已投递通知 | `sqlite3 <nc_db> "DELETE FROM record WHERE delivered_date < strftime('%s','now','-30 days'); VACUUM;"` 然后 `killall NotificationCenter` | 低 | `tasks.sh:1417-1468` |
| O19 | `coreduet_cleanup` | 清 90 天前使用追踪数据 | `sqlite3 knowledgeC.db "DELETE FROM ZOBJECT WHERE ZCREATIONDATE < (strftime('%s','now','-90 days') - strftime('%s','2001-01-01')); VACUUM;"` | 低 | `tasks.sh:1512-1580` |

### 5.1 各任务的具体阈值

**O1 `cache_refresh`**（`tasks.sh:196-201`）：三个目标
```
~/Library/Caches/com.apple.QuickLook.thumbnailcache
~/Library/Caches/com.apple.iconservices.store
~/Library/Caches/com.apple.iconservices
```
先 `qlmanage -r cache` 与 `qlmanage -r`，再 `safe_remove`。

**O5 `sqlite_vacuum`**（`tasks.sh:504-694`）—— 检查最密的一个：
1. `pgrep -x Mail|Safari|Messages` 任一命中 → `skipped` 并提示关掉；
2. `sqlite3` 不存在 → `unavailable`；
3. 目标：`~/Library/Mail/V*/MailData/Envelope Index*`、`~/Library/Messages/chat.db`、`~/Library/Safari/{History,TopSites}.db`；
4. 跳过 `*-wal`、`*-shm`；
5. `file -b` 必须含 `SQLite`；
6. 文件 > `MOLE_SQLITE_MAX_SIZE`=104857600（100MB）→ 跳过（`tasks.sh:19`、`584`）；
7. `PRAGMA page_count; PRAGMA freelist_count;`，若 `freelist*100 < page_count*5`（空闲页 < 5%）→ 判「已最优」，跳过（`tasks.sh:606`）；
8. `PRAGMA integrity_check;` 必须返回 `ok`，否则跳过；
9. `VACUUM;`。

**O4 DNS 刷新的 VPN 守卫**（`tasks.sh:120-163`）：
- `scutil --nc list` 输出有 `^\* \(Connected\)` → 有 VPN；
- `route -n get default` 的 `interface` 是 `utun<N>` → 有 VPN；
- 有 VPN → 跳过（VPN 客户端会把 SIGHUP 当网络变化并重连）；
- 无默认路由且报 `not in table` → 判定「确认无 VPN」，不算未知；
- 其他异常 → 未知，跳过。

**O10 Spotlight**（`tasks.sh:855-958`）：
- `mdutil -s /` 报 `Indexing disabled` → 跳过；
- 只在**接交流电**时评估（`is_ac_power`）；
- 用 `mdfind "kMDItemFSName == 'Applications'"` 测速两次，间隔 1s；
- 阈值 `MOLE_OPTIMIZE_SPOTLIGHT_SLOW_SEC`，默认 3s；
- 两次都慢（`slow_count >= 2`）才重建。

**O12 periodic**（`tasks.sh:1264-1313`）：`periodic` 命令在 macOS 26+ 已移除 → `unavailable`；`/var/log/daily.out` 的 mtime 距今 < 7 天 → `unchanged`。

**O14 disk verify**（`tasks.sh:1470-1510`）：注释明确说 `diskutil verifyVolume` 在 APFS 不一致时会触发不可 SIGKILL 的内核 I/O，可能冻机，所以 `MOLE_ENABLE_DISK_VERIFY` 默认 0。

**O18 通知库路径**（`tasks.sh:1399-1414`）：
1. `~/Library/Group Containers/group.com.apple.usernoted/db2/db`（macOS 15+）；
2. `$(getconf DARWIN_USER_DIR)/com.apple.notificationcenter/db2/db`（旧版）。
体积 < 51200 KB（50MB）→ 不动（`tasks.sh:1435-1436`）。

**O19 CoreDuet**（`tasks.sh:1512-1580`）：`~/Library/Application Support/Knowledge/knowledgeC.db` + `-wal` + `-shm` 合计 < 102400 KB（100MB）→ 不动。注意 SQL 里减了 `strftime('%s','2001-01-01')`，因为 CoreTime 是 Mac epoch。

### 5.2 被明确移除的优化任务（`tasks.sh:291-359`、`690-694`）

| 任务 | 移除理由（源码原话要点） |
|---|---|
| `opt_maintenance_scripts` | macOS 用 launchd 自己轮转日志 |
| `opt_radio_refresh` | 会打断 WiFi/蓝牙连接 |
| `opt_swap_cleanup` | 直接操作虚拟内存有系统崩溃风险 |
| `opt_startup_cache` | 现代 macOS 没有这个机制 |
| `opt_local_snapshots` | 删用户 TM 恢复点，破坏备份连续性 |
| `opt_startup_items_cleanup` | 有删掉合法 app helper 的风险 |
| `opt_dyld_cache_update` | 收益低、耗时长、macOS 自己管 |
| `opt_system_services_refresh` | 杀系统服务有丢数据风险 |

另有一条重要事实（`bin/clean.sh:1805-1812`）：**LaunchServices 重建已被彻底移除**。原因：`lsregister -u` 在 macOS 15+ 对已消失的 app 一律返回 `-10814`，而这个集合恰好就是它唯一能针对的对象；且重建会让运行中的 VPN 扩展被读成「重装」。**Python 实现不要提供 LaunchServices 重建。**

---

## 6. 对 Python 实现的建议：按「收益/风险」排序的 15 条

排序依据：单条规则的**预期回收量** × **失败后果的可逆性**（能不能一条命令重建）。

**「收益依据」列分两类，请勿混淆**：
- 标 **【源码实测】** 的，是 Mole 注释里写下的数字，本文照抄；
- 标 **【估计】** 的，是本文作者的判断，**源码里没有对应数字**。§6 是全文唯一允许出现估计的地方，其余章节只有源码事实。

| 排名 | 规则 | 收益依据 | 风险 | 必做的守卫 |
|---|---|---|---|---|
| **1** | `~/Library/Developer/Xcode/DerivedData/*` | 【估计】每个项目 GB 级。源码只说明「按项目逐个删并报项目数」，**未给数字**（`app_caches.sh:93`、`183`） | 极低（下次构建重建） | 无 Xcode/xcodebuild/xctest 进程；size 前后各查一次（`app_caches.sh:95-260`） |
| **2** | `mo purge` 式项目构建产物（`node_modules` / `target` / `build` / `.next` / `dist` / `.venv`） | 【估计】单个 `node_modules` 数百 MB 至 GB。源码无数字 | 低但**面广**：误删一个有未提交源码的 `build/` 是不可逆的 | Git 跟踪检查 + `.git`/`*-keypair.json` 探测 + 7 天活动窗口 + 物理包含性（§2.2–2.6） |
| **3** | Chrome / Edge / Arc 的 `Application Support` 缓存叶子 + `Service Worker/CacheStorage` | 【源码实测】Chrome 端上模型 `OptGuideOnDeviceModel` 注释写「often multiple GB」（`user.sh:1993`）；其余叶子无数字 | 低（SW 会在下次打开时重新 precache） | 进程守卫 + 18 个 `PROTECTED_SW_DOMAINS` + **绝不删 ScriptCache** |
| **4** | `~/Library/Caches/*` 整体清扫 | 【估计】单机 GB 级。源码无数字 | **中**：这条 glob 面最广，全靠白名单兜底 | 必须完整移植 `should_protect_path` + `SAFETY_WHITELIST_PATTERNS`；先排除 DENO_DIR |
| **5** | `~/Library/Caches/com.apple.dt.Xcode/*` + `Xcode/Products/*` | 【估计】GB 级。源码无数字 | 低 | 同 #1 |
| **6** | `~/Library/Developer/Xcode/*DeviceSupport/*`（保留最新 2 个） | 【源码实测】注释：每个版本 1–3GB（`dev.sh:2109-2111`） | 低（连设备即重建） | 保留最新 2 个；保留目录内 `Symbols/.../Caches/*` 与 `*.log` 也清 |
| **7** | 微信/企微容器缓存 | 【源码实测】~1.4GB（`app_caches.sh:633-643`：WeCom cefcache 482MB + Cache 424MB + Code Cache 48MB + WXWork Log 81MB + WeChat log 187MB + WMPF 161MB） | 中（必须精确分界） | 严格按 `app_caches.sh:645-651` 的非目标清单排除 `xwechat_files`、`Messages1`、`cefcache/<profile>` 的 cookies |
| **8** | 飞书 / Lark Service Worker CacheStorage | 【源码实测】27GB / 9 个 origin bucket / 最老文件约 11 个月（`app_caches.sh:472-473`） | 低（文档在服务端，auth 在 Cookies） | 只清 CacheStorage；profile 根被符号链接重定向就拒绝 |
| **9** | 各包管理器 owner 命令（npm/pnpm/bun/uv/pip/conda/go/cargo） | 【估计】`~/.npm/_cacache` 与 pnpm store 均可达 GB 级。源码无数字 | 低（有 owner 命令就不手删） | **owner 命令失败不降级为直接删**（`dev.sh:53-56`）；pnpm 有进程就跳过 |
| **10** | `~/.gradle/{caches/build-cache-*,notifications,daemon,workers}` | 【估计】GB 级。源码无数字 | 低 | 无 Gradle daemon 进程；**默认白名单里已有 `~/.gradle/caches/*` 与 `daemon/*`**，移植时要保留这个「默认不动」的语义 |
| **11** | `~/Library/Caches/Homebrew/downloads/*` | 【估计】数百 MB 至 GB。源码无数字 | 低 | 只清 `downloads/`；**`locks/` 不碰**（删了会让运行中的 `brew fetch` 报 `dir_s_rmdir`，issue #1594）；不碰 `api/`、`bootsnap/` |
| **12** | 浏览器/Electron 的 `Application Support` 通用 Electron 缓存（`Cache`/`Code Cache`/`GPUCache`/`DawnGraphiteCache`/`DawnWebGPUCache`/`Crashpad/completed`） | 【估计】每 app 数百 MB。源码无数字 | 低 | 只在「目录里已有可重建标记」时才把 `Cache`/`CachedData` 加进候选（`user.sh:2536-2553`） |
| **13** | `~/Library/Caches/*` 里的 AI 工具（ChatGPT / Claude Desktop / LM Studio / Antigravity / Qoder / Codex） | 【估计】单 app 数百 MB 至 GB。源码只在 Codex 处给了 313,860 KiB（`dev.sh:4241`） | 低 | `~/.claude`、`~/.codex`、`~/.gemini/tmp` 绝不碰；`~/.ollama/models` 在白名单 |
| **14** | `/Library/Caches` + `/Library/Logs/DiagnosticReports` + `/private/var/log`（7 天） | 【估计】GB 级。源码无数字 | **中**：需 sudo；删错系统缓存可能让 UI 异常 | 严格按 `system.sh:238-304` 的模式与 maxdepth；`/Library/Updates` 与 `/macOS Install Data` 永不碰 |
| **15** | `~/Library/Logs/*`（无年龄过滤） | 【估计】数百 MB 至 GB。源码无数字 | **中**：无年龄过滤，删掉的是全部日志 | 保留 `DiagnosticReports` 目录本身；Mole 自己保留 `~/Library/Logs/mole`（`app_protection.sh:507-510`） |

### 6.1 不值得移植的（收益/风险比差）

| 规则 | 理由 |
|---|---|
| Xcode DocumentationCache（#10 in §1.8） | 需 sudo、只在有多个 index 时动手、回收量通常 <1GB |
| GPU cache（`/private/var/folders/*/*/C/*/com.apple.metal`） | 需扫描 maxdepth 8 的 `/private/var/folders`；注释实测 217k 目录 / 19s（prune 后 0.09s）。收益小、扫描贵 |
| `install macOS*.app` | 14 天 + 6 条前置条件，一年碰不上一次 |
| Playwright 泄漏进程清理 | 需要 `ps` 解析 + `ppid==1` + 命令行匹配 + 两次 `kill` 升级；误杀风险不低 |
| Codex daemon releases | 需要 `lockf` 持锁（`dev.sh:4757-4760`）；Python 侧要用 `fcntl` 重写，收益小 |
| 剪映 / Autodesk Fusion / NeatDM | 每条的判定逻辑都是几十行特例，回收量不抵维护成本 |
| 所有 optimize 任务 | 见下 |

### 6.2 optimize 里唯一值得移植的两条

- **O1 `cache_refresh`**：`qlmanage -r cache` + `qlmanage -r` + 删 3 个目录。零 sudo、零判断、效果可见。
- **O4 `network_optimization`**：`dscacheutil -flushcache` + `killall -HUP mDNSResponder`，加 VPN 守卫。需 sudo。

其余 17 条要么需要 sudo、要么改系统偏好、要么动 SQLite 数据库，对一个「清理工具」来说属于另一个产品范畴。

### 6.3 移植时最容易踩的三个坑

1. **不要用「锁文件存在」判断可重建性。** Mole 用 Git 跟踪，不用 `package-lock.json` 之类。二者的区别：一个 `build/` 目录里可能既有锁文件也有被跟踪的源码。
2. **不要用目录 mtime 判断活跃度。** 目录 mtime 只在增删改名时变；`classify_purge_activity` 用的是 `find <dir> -type f -mtime -7 -print -quit`（`project.sh:1095-1096`）。GPU 缓存同理（`system.sh:33-52`）。
3. **「不知道」必须当「危险」。** 所有探测（进程、lsof、Git、mtime、sudo 可用性、VPN 状态）都有三态，`2` 一律不放行。Mole 在 `_mole_paths_have_open_handle` 里甚至专门把 `MO_DEBUG` 关掉，因为 debug 输出会污染 lsof 的 stderr，让「确认空闲」降级成「不知道」（`file_ops.sh:773-777`）。
