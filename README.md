# Wwise Playlist Automator

带 UI 的 Wwise WAAPI 批量搭建工具：

- 批量创建留声机 `Formal` / `PrePlay` 音乐容器层级。
- 批量创建对应 `Event + 3 Actions + Notes`。
- 支持一键执行（先容器后 Event）。

## 依赖

- Python 3.10+
- `websocket-client`

```bash
pip install websocket-client
```

## 运行

```bash
python3 wwise_playlist_automator.py
```

## 使用说明

1. 先在本机打开 Wwise 工程，并启用 WAAPI（默认 `ws://127.0.0.1:8080/waapi`）。
2. 在工具中输入编号（支持逗号/空格/换行分隔），格式 `X_X_X`。
3. 若需要建音乐容器，填写曲目数量（例如 `40`，会创建 `M_01` 到 `M_40`）。
4. 选择：
   - `仅创建音乐容器`
   - `仅创建 Event`
   - `一键执行（容器+Event）`

## 规则实现摘要

- Formal SwitchContainer 固定使用 `{67EF15FE-7263-44A2-B790-4E113AE75FA9}`。
- PrePlay SwitchContainer 运行时按路径查询。
- Event Actions 顺序固定：`Stop(2)` → `SetState(22)` → `Play(1)`。
- `Playlist_Enabled` State 固定使用 `{C7B549E2-9323-4DC4-9231-F1586DE51A04}`。
- PrePlay Event 名称自动将 `PrePlay` 转换为 `Preplay`。
- Event Notes：
  - Formal：`播放留声机对应音乐`
  - PrePlay：`播放留声机对应试听`

## 备注

- 脚本对 `ak.wwise.core.object.get` 的结果统一通过 `.get("return", [])` 安全读取。
- PrePlay Playlist 无限循环配置使用 WAAPI `object.set` 进行 best-effort 写入（不同 Wwise 版本字段可能存在差异）。
