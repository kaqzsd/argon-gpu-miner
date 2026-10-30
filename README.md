# Argon GPU Miner

ARG / Argonmine 的实验版 Linux GPU SOLO 矿工，适合 NVIDIA 租赁服务器。

**RTX 4090 已实测；RTX 5090 尚未实机验证。** 5090 使用同一 OpenCL 路径，每次启动先将 GPU 哈希与 CPU 逐字节比对，失败即退出。默认 `batch=1024` 使用约 8 GiB GPU 工作区；不承诺 5090 的具体速度。

没有内置收款地址、邀请人或开发者抽成。启动时填自己的 ARG 地址；不需要私钥或助记词。

## 租机快速开始

选择 **Linux x86_64、NVIDIA 驱动正常的镜像**。推荐 Ubuntu 24.04 / Python 3.12；支持 Python 3.10+。`nvidia-smi` 应能识别显卡，镜像还需暴露 NVIDIA OpenCL 运行库。

```bash
git clone https://github.com/kaqzsd/argon-gpu-miner.git
cd argon-gpu-miner
bash install.sh
./miner.sh start --address YOUR_ARG_ADDRESS --batch 1024
./miner.sh logs --follow
```

把 `YOUR_ARG_ADDRESS` 换成你的 `arg1...` 收款地址。启动后可以断开 SSH；看日志时按 Ctrl+C 只退出日志跟踪，矿工继续运行。

如果镜像缺少 Python venv 或 OpenCL loader，在 Debian/Ubuntu 上先运行：

```bash
sudo bash install.sh --system-deps   # 已经是 root 时去掉 sudo
bash install.sh
```

系统依赖安装只涉及 Python、venv、CA 证书和 OpenCL loader，不安装或更换 NVIDIA 驱动，不更改功耗/超频，不启停其他矿工。

## 离线依赖包

[Releases](https://github.com/kaqzsd/argon-gpu-miner/releases) 提供两种压缩包和 `SHA256SUMS`：

- `argon-gpu-miner-v0.1.0-source.tar.gz`：源码和安装脚本，依赖从 PyPI 下载。
- `argon-gpu-miner-v0.1.0-linux-x86_64-py312-offline.tar.gz`：附带 Linux x86_64 / Python 3.12 依赖 wheels；此环境下 `install.sh` 自动离线安装。仍需系统 Python、venv 和宿主机 NVIDIA 驱动。

Ubuntu 24.04 租机免 Git 下载方式：

```bash
curl -fL -o argon.tar.gz https://github.com/kaqzsd/argon-gpu-miner/releases/download/v0.1.0/argon-gpu-miner-v0.1.0-linux-x86_64-py312-offline.tar.gz
tar -xzf argon.tar.gz
cd argon-gpu-miner-v0.1.0
bash install.sh
./miner.sh start --address YOUR_ARG_ADDRESS --batch 1024
```

其他 Python 版本会改用 PyPI 解析兼容依赖。不要把旧虚拟环境从别的机器直接复制过来。

## 常用操作

```bash
./miner.sh doctor                         # 驱动/OpenCL 检测，不挖矿
./miner.sh devices                        # 可选 GPU 序号
./miner.sh self-test --full                # 768 个完整哈希交叉校验，不联网
./miner.sh benchmark                      # 256/512/1024/2048 批次离线测速
./miner.sh status                         # 进程、地址、最新速度、429 与整块数量
./miner.sh status --json
./miner.sh logs --follow
./miner.sh stop
./miner.sh start                          # 使用本机 config.json 里已保存的地址与参数
```

显存不足时降低批次：256=2 GiB、512=4 GiB、1024=8 GiB、2048=16 GiB，另有运行库开销。改参数前先停止：

```bash
./miner.sh stop
./miner.sh start --batch 512 --device 0
```

单个进程只使用一张卡，`--device` 是 `devices` 输出的 OpenCL 序号。当前不提供多卡联合挖矿；同机同一钱包/服务端的第二个进程会被单实例锁拒绝。进程异常退出或服务器重启后需手动启动，没有自动重启或开机服务。

本机配置 `config.json` 权限为 0600，未纳入 Git。状态在 `runtime/status.json`，约每 10 秒更新。`runtime/miner.log` 最多保留当前 10 MiB 加 5 份备份；启动失败先看 `runtime/launcher.log`。停止命令核对 PID、启动时间和脚本路径，不按进程名批量杀程序。

## 算法与联网行为

Argon2id v19，`t=1, m=8192 KiB, p=1, out=32`，salt 为 `Argonmine/pow/v1`。GPU 完成 Blake2b 初始化、Argon2 内存填充和收尾，每个候选解再由 CPU 复算。

一个取任务线程为所有计算批次共享工作，每次成功后等待 20 秒。429 时暂停计算，遵守 Retry-After（秒数或 HTTP 日期），并按 30–300 秒退避加抖动。不会轮换 IP 或地址绕过限流；这些措施不能保证官方服务永不返回 429。

矿工校验 coinbase 收款地址、国库、Merkle 证明和目标，拒绝陌生收款方与邀请分成。2026-10-30 曾观察到官方工作服务只给矿工 49 ARG、国库 0.5 ARG；相对文档额外少 0.5。当前只允许最多再少一个国库份额并打印 NOTICE，更低则拒绝。少付部分没有转给本软件。

这是 **SOLO 整块挖矿**，`accepted` 表示服务器接受的整块，不是矿池 share；短时间为 0 很正常。H/s 是实际完成的哈希计数，但 20 秒刷新期间工作可能已过期，不能把全部哈希计为链上有效工作。

## 实测与限制

2026-10-30，RTX 4090，Linux / OpenCL，GPU/CPU 768 个完整哈希比对通过：

| batch | GPU 工作区 | 15 秒基准 |
|---:|---:|---:|
| 256 | 2 GiB | 31.4 kH/s |
| 512 | 4 GiB | 51.8 kH/s |
| 1024 | 8 GiB | 55.2 kH/s |

4090 10 分钟真实联网测试平均 53.6 kH/s，约 3,214 万哈希，30 次取任务均成功、429 为 0；运行阶段约 353 W、60–61°C。该次测试未挖到整块，尚无主网提交接受及最终奖励到账的验证。5090、Windows 和多卡尚未实测。

## 测试与来源

```bash
python3 -m venv .test-venv
.test-venv/bin/pip install -r requirements-cpu.txt
.test-venv/bin/python -m unittest discover -s tests -v
```

测试使用合成地址和本机模拟服务器，不访问主网。模拟 accepted 不代表主网出块。GitHub Actions 对 Python 3.10 与 3.12 运行协议、限流恢复、nonce 精度和 Linux 启停测试。

- [官方协议和源码](https://github.com/argonchain-source/argonchain)
- [官方挖矿说明](https://docs.argonmine.com/mining)
- [Argon2 GPU 内核上游](https://github.com/WebDollar/argon2-gpu)，Ondrej Mosnáček 等，MIT 许可证见 `ARGON2-GPU-LICENSE`。

实验性软件，非官方矿工。无收益保证；仅在你拥有或获准使用的硬件上运行。
