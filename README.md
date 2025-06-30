# geotag

基于 exiftool 和 GPX 文件的批量图片地理标记 CLI 工具。

## 特性
- 支持多线程批量处理图片
- 支持多个目录同时处理
- 支持灵活的日期表达式过滤（如 `date > 20250101 and date < 20260101`），也可以不过滤
- 支持 dry-run 预览
- 自动分发任务到多线程

## 安装

1. 安装 Python 3.12+
2. 安装 exiftool（需加入系统 PATH）
3. 安装本工具：

```sh
pip install .
```

或直接通过 PyPI（发布后）：

```sh
pip install geotag
```

### 使用 pipx 安装

推荐使用 [pipx](https://github.com/pypa/pipx) 进行全局隔离安装：

```sh
pipx install geotag
```

安装后可直接在命令行使用 `geotag` 命令。

## 用法

```sh
geotag --dirs <目录1> [目录2 ...] --gpx <gpx文件1> [gpx文件2 ...] [--filter <日期表达式>] [--threads N] [--dry-run]
```

### 示例

```sh
# 处理单个目录，不过滤日期
geotag --dirs ./photos --gpx track1.gpx

# 处理多个目录，应用日期过滤
geotag --dirs ./photos1 ./photos2 --gpx track1.gpx track2.gpx --filter "date > 20250101"

# 复杂日期过滤和自定义线程数
geotag --dirs ./photos --gpx track1.gpx track2.gpx --filter "date > 20250101 and date < 20260101" --threads 8

# 预览模式（不实际修改文件）
geotag --dirs ./photos --gpx track1.gpx --filter "date > 20250101" --dry-run
```

### 参数说明

- `--dirs`：待处理图片的文件夹（可以指定多个）
- `--gpx`：GPX轨迹文件（可以指定多个）
- `--filter`：可选的日期过滤表达式，如果不指定则处理所有文件
- `--threads`：线程数（可选，默认自动）
- `--dry-run`：仅显示将要执行的命令，不实际修改文件

## 日期表达式语法
- 支持 `date >`, `<`, `=`, `>=`, `<=`, `!=` 等比较
- 支持 `and`, `or` 逻辑组合，支持括号
- 日期格式支持：
  - `YYYYMMDD`（如 20250101）
  - `YYYY-MM-DD`、`YYYY/MM/DD`
  - ISO 8601（如 2025-05-25T10:00:00+08:00）
  - Unix 时间戳（秒）

## 依赖
- Python 3.12+
- exiftool

## 发布到 PyPI
1. 安装构建工具：
   ```sh
   pip install build twine
   ```
2. 构建包：
   ```sh
   python -m build
   ```
3. 上传：
   ```sh
   twine upload dist/*
   ```

## License
MIT
