import argparse
import os
import subprocess
import threading
import multiprocessing
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Tuple, Optional
import time
from dateexpr import DateExpressionParser  # 替换为自定义表达式解析器


class GeoTagger:
    def __init__(
        self,
        directory: str,
        gpx_files: List[str],
        date_expression: str,
        threads: Optional[int] = None,
        dry_run: bool = False,
    ):
        self.directory = Path(directory)
        self.gpx_files = [Path(gpx) for gpx in gpx_files]
        self.date_parser = DateExpressionParser(date_expression)
        self.threads = threads if threads else multiprocessing.cpu_count() * 2
        self.dry_run = dry_run
        self.supported_extensions = {
            ".jpg",
            ".jpeg",
            ".tiff",
            ".tif",
            ".cr2",
            ".nef",
            ".arw",
            ".dng",
            ".heic",
            ".png",
        }

    def find_files(self) -> List[Tuple[Path, datetime]]:
        """查找目录下符合日期表达式的所有文件（不再按扩展名过滤）"""
        files_with_dates = []

        print(f"正在扫描目录: {self.directory}")
        for file_path in self.directory.rglob("*"):
            if file_path.is_file():
                try:
                    # 获取文件修改时间
                    mtime = datetime.fromtimestamp(
                        file_path.stat().st_mtime, tz=timezone.utc
                    )
                    if self.date_parser.evaluate(mtime):
                        files_with_dates.append((file_path, mtime))
                except OSError as e:
                    print(f"无法获取文件 {file_path} 的修改时间: {e}")
                except ValueError as e:
                    print(f"日期表达式评估错误: {e}")

        print(f"找到 {len(files_with_dates)} 个符合条件的文件")
        return files_with_dates

    def sort_files_by_date(
        self, files_with_dates: List[Tuple[Path, datetime]]
    ) -> List[Path]:
        """按修改日期排序文件"""
        sorted_files = sorted(files_with_dates, key=lambda x: x[1])
        return [file_path for file_path, _ in sorted_files]

    def chunk_files(self, files: List[Path]) -> List[List[Path]]:
        """将文件列表平均分段，分发给每个线程（优化：每个线程分到的文件数最多只相差1）"""
        n = len(files)
        t = min(self.threads, n)
        base = n // t
        remainder = n % t
        chunks = []
        start = 0
        for i in range(t):
            end = start + base + (1 if i < remainder else 0)
            chunks.append(files[start:end])
            start = end
        return chunks

    def process_chunk(self, chunk: List[Path], thread_id: int):
        """处理一个文件分段（批量调用exiftool）"""
        print(f"线程 {thread_id} 开始处理 {len(chunk)} 个文件")

        if not chunk:
            print(f"线程 {thread_id} 分段为空，跳过")
            return

        try:
            # 构建exiftool命令，使用GPX文件进行地理标记
            cmd = [
                "exiftool",
                "-overwrite_original",
            ]

            # 添加所有GPX文件参数
            for gpx_file in self.gpx_files:
                cmd.extend(["-geotag", str(gpx_file)])

            # 添加所有目标文件
            cmd.extend([str(file_path) for file_path in chunk])

            if self.dry_run:
                # Dry run模式：只显示将要执行的命令
                cmd_str = " ".join(f'"{arg}"' if " " in arg else arg for arg in cmd)
                print(f"线程 {thread_id} DRY RUN: {cmd_str}")
            else:
                result = subprocess.run(cmd, capture_output=True, text=True)

                if result.returncode == 0:
                    print(f"线程 {thread_id} 成功处理 {len(chunk)} 个文件")
                else:
                    print(f"线程 {thread_id} 处理失败: {result.stderr.strip()}")

        except subprocess.TimeoutExpired:
            if not self.dry_run:
                print(f"线程 {thread_id} 批量处理超时")
        except Exception as e:
            print(f"线程 {thread_id} 错误: {e}")

        print(f"线程 {thread_id} 完成处理")

    def run(self):
        """执行整个geo tagging流程"""
        # 1. 查找文件
        files_with_dates = self.find_files()
        if not files_with_dates:
            print("没有找到符合条件的文件")
            return

        # 2. 按修改日期排序
        sorted_files = self.sort_files_by_date(files_with_dates)
        print(
            f"文件已按修改时间排序，最早: {files_with_dates[0][1]}, 最晚: {files_with_dates[-1][1]}"
        )

        # 3. 分段
        chunks = self.chunk_files(sorted_files)
        print(f"文件已分为 {len(chunks)} 个分段，使用 {len(chunks)} 个线程")

        # 4. 多线程处理
        threads = []
        start_time = time.time()

        for i, chunk in enumerate(chunks):
            thread = threading.Thread(target=self.process_chunk, args=(chunk, i + 1))
            threads.append(thread)
            thread.start()

        # 等待所有线程完成
        for thread in threads:
            thread.join()

        end_time = time.time()
        print(f"所有处理完成，耗时: {end_time - start_time:.2f} 秒")


def main():
    parser = argparse.ArgumentParser(
        description="使用GPX文件为图像文件添加GPS地理标签",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例用法:
  python main.py /path/to/photos track1.gpx track2.gpx "date > 20250101"
  python main.py /path/to/photos *.gpx "date > 20250101 and date < 20260101" --threads 8
        """,
    )

    parser.add_argument("directory", help="要处理的图像文件目录")
    parser.add_argument("gpx_files", nargs="+", help="一个或多个GPX轨迹文件")
    parser.add_argument(
        "date_expression",
        help='日期过滤表达式，如 "date > 20250101" 或 "date > 20250101 and date < 20260101"',
    )
    parser.add_argument(
        "--threads",
        "-t",
        type=int,
        default=None,
        help=f"线程数量 (默认: {multiprocessing.cpu_count() * 2})",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="仅显示将要执行的命令，不实际修改文件"
    )

    args = parser.parse_args()

    # 验证目录存在
    if not os.path.isdir(args.directory):
        print(f"错误: 目录不存在: {args.directory}")
        return 1

    # 验证GPX文件存在
    for gpx_file in args.gpx_files:
        if not os.path.isfile(gpx_file):
            print(f"错误: GPX文件不存在: {gpx_file}")
            return 1

    # 验证日期表达式
    try:
        # 创建一个测试解析器来验证表达式格式
        test_parser = DateExpressionParser(args.date_expression)
        test_date = datetime.now(tz=timezone.utc)
        test_parser.evaluate(test_date)  # 测试表达式是否可以正常执行
    except ValueError as e:
        print(f"错误: 日期表达式无效: {e}")
        return 1

    # 检查exiftool是否可用
    try:
        subprocess.run(["exiftool", "-ver"], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("错误: 未找到 exiftool，请确保已安装并在PATH中")
        return 1

    # 设置默认线程数
    threads = args.threads if args.threads else multiprocessing.cpu_count() * 2

    print(f"开始处理...")
    print(f"目录: {args.directory}")
    print(f"GPX文件: {', '.join(args.gpx_files)}")
    print(f"日期表达式: {args.date_expression}")
    print(f"线程数: {threads}")
    if args.dry_run:
        print("模式: DRY RUN (不会修改文件)")
    print("-" * 50)

    # 创建并运行GeoTagger
    tagger = GeoTagger(
        args.directory, args.gpx_files, args.date_expression, threads, args.dry_run
    )
    tagger.run()

    return 0


if __name__ == "__main__":
    exit(main())
