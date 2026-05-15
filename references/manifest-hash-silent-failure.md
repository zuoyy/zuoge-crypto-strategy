# Manifest Hash 陷阱：策略静默不加载

2026-05-15 实战：修改 `workflow_distilled_funnel_0_1_0.py` 后部署重启，candidate 池始终为空，但进程无任何 exception 日志。排查发现 `load_strategy_file()` 因 manifest `code_hash` 不匹配拒绝加载策略，导致 `discover()` 从未被调用。

## 故障链路

```
修改 .py 文件 → 复制到 enabled 目录 → 重启进程
→ load_enabled_strategies() 读取 manifest code_hash
→ sha256_file() 算实际 hash → 不匹配 → raise ValueError
→ 策略未加载 → handles = []
→ handle_universe() 遍历空 handles → discover() 从未调用
→ candidate 池永远为空
→ 进程看似正常运行（NATS 连接、订阅都在），但策略不存在
```

## 关键代码

`strategy/runtime/strategy_manager.py`:

```python
def load_strategy_file(path: Path) -> StrategyHandle:
    manifest = read_manifest(path)
    actual_hash = sha256_file(path)
    expected_hash = manifest.get("code_hash", manifest.get("code_sha256", ""))
    if expected_hash != actual_hash:
        raise ValueError(f"code hash mismatch for {path.name}: expected {expected_hash}, got {actual_hash}")
```

注意：manifest 可能同时有 `code_hash` 和 `code_sha256` 两个字段，优先读 `code_hash`。

## enabled 目录的真实加载路径

`enabled_dir()` 通过 `Path(__file__).resolve()` 解析 symlink：

```python
def enabled_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "strategies" / "enabled"
```

Python 导入时 `__file__` 已是解析后的真实路径，指向 release 目录。因此真实加载路径是 `releases/<id>/strategy/strategies/enabled/`，而非顶层 `/opt/homebrew/var/crypto-trader/strategies/enabled/`。但两个路径的 manifest 都应更新保持一致。

## 快速诊断命令

```bash
cd /opt/homebrew/var/crypto-trader/current && python3 -c "
import sys; sys.path.insert(0,'strategy')
from runtime.strategy_manager import load_enabled_strategies
h,e = load_enabled_strategies()
print(f'Handles: {len(h)}, Errors: {len(e)}')
for x in e: print(f'  {x[\"path\"]}: {x[\"error\"]}')
"
```

期望输出: `Handles: 1, Errors: 0`

## 生产调试注意事项

- Python stdout 写入文件时全线缓冲，`print()` 可能不立即刷盘。文件型 debug 用 `open("/tmp/debug.log","a").write(...)` 或 `os.system("echo ... >> /tmp/debug.log")`。
- SlowConsumer 错误持续产生 ≠ 策略在正常工作。错误来自 NATS client 层的消息丢弃，callback 可能未被调用。
- 重启后 stdout 日志 0 字节但进程 CPU 非零 → 策略未加载/event loop 阻塞，非正常空闲。
