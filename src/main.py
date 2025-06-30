import argparse
import os
import subprocess
import multiprocessing
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Tuple, Optional
import time
from src.dateexpr import DateExpressionParser  # 替换为自定义表达式解析器
import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed


class GeoTagger:
    def __init__(
        self,
        directories: List[str],
        gpx_files: List[str],
        date_expression: Optional[str] = None,
        threads: Optional[int] = None,
        dry_run: bool = False,
    ):
        self.directories = [Path(directory) for directory in directories]
        self.gpx_files = [Path(gpx) for gpx in gpx_files]
        self.date_parser = (
            DateExpressionParser(date_expression) if date_expression else None
        )
        self.threads = threads if threads else multiprocessing.cpu_count() * 2
        self.dry_run = dry_run

    def find_files(self) -> List[Tuple[Path, datetime]]:
        """多线程查找多个目录下符合日期表达式的所有文件（不再按扩展名过滤）"""
        files_with_dates = []
        all_files = []

        # 从所有目录收集文件
        for directory in self.directories:
            print(f"正在扫描目录: {directory}")
            all_files.extend(list(directory.rglob("*")))

        def check_file(file_path):
            if file_path.is_file():
                try:
                    mtime = datetime.fromtimestamp(
                        file_path.stat().st_mtime, tz=timezone.utc
                    )
                    # 如果没有日期过滤器，或者日期符合条件，则包含该文件
                    if self.date_parser is None or self.date_parser.evaluate(mtime):
                        return (file_path, mtime)
                except OSError as e:
                    print(f"无法获取文件 {file_path} 的修改时间: {e}")
                except ValueError as e:
                    print(f"日期表达式评估错误: {e}")
            return None

        with ThreadPoolExecutor(max_workers=self.threads) as executor:
            futures = {
                executor.submit(check_file, file_path): file_path
                for file_path in all_files
            }
            for f in tqdm.tqdm(
                as_completed(futures), total=len(futures), desc="扫描文件", unit="file"
            ):
                result = f.result()
                if result:
                    files_with_dates.append(result)

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
        # 只在 dry_run 时输出命令，其他情况下减少日志
        if not chunk:
            return

        try:
            cmd = [
                "exiftool",
                "-overwrite_original",
            ]
            for gpx_file in self.gpx_files:
                cmd.extend(["-geotag", str(gpx_file)])
            cmd.extend([str(file_path) for file_path in chunk])

            if self.dry_run:
                cmd_str = " ".join(f'"{arg}"' if " " in arg else arg for arg in cmd)
                print(f"线程 {thread_id} DRY RUN: {cmd_str}")
            else:
                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode != 0:
                    print(f"线程 {thread_id} 处理失败: {result.stderr.strip()}")
        except subprocess.TimeoutExpired:
            if not self.dry_run:
                print(f"线程 {thread_id} 批量处理超时")
        except Exception as e:
            print(f"线程 {thread_id} 错误: {e}")

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

        # 4. 多线程处理（用ThreadPoolExecutor重构）
        start_time = time.time()
        with ThreadPoolExecutor(max_workers=self.threads) as executor:
            futures = [
                executor.submit(self.process_chunk, chunk, i + 1)
                for i, chunk in enumerate(chunks)
            ]
            for f in tqdm.tqdm(
                as_completed(futures), total=len(futures), desc="线程进度", unit="chunk"
            ):
                f.result()  # 捕获异常
        end_time = time.time()
        print(f"所有处理完成，耗时: {end_time - start_time:.2f} 秒")


def main():
    parser = argparse.ArgumentParser(
        description="使用GPX文件为图像文件添加GPS地理标签",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例用法:
  python main.py --dirs /path/to/photos1 /path/to/photos2 --gpx track1.gpx track2.gpx
  python main.py --dirs /path/to/photos --gpx track1.gpx track2.gpx --filter "date > 20250101"
  python main.py --dirs /path/to/photos1 /path/to/photos2 --gpx *.gpx --filter "date > 20250101 and date < 20260101" --threads 8
        """,
    )

    parser.add_argument(
        "--dirs",
        "--directories",
        nargs="+",
        required=True,
        help="要处理的图像文件目录（可以是多个）",
    )
    parser.add_argument(
        "--gpx", "--gpx-files", nargs="+", required=True, help="一个或多个GPX轨迹文件"
    )
    parser.add_argument(
        "--filter",
        "--date-expression",
        dest="date_expression",
        default=None,
        help='日期过滤表达式，如 "date > 20250101" 或 "date > 20250101 and date < 20260101"。如果不提供则不过滤',
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
    for directory in args.dirs:
        if not os.path.isdir(directory):
            print(f"错误: 目录不存在: {directory}")
            return 1

    # 验证GPX文件存在
    for gpx_file in args.gpx:
        if not os.path.isfile(gpx_file):
            print(f"错误: GPX文件不存在: {gpx_file}")
            return 1

    # 验证日期表达式（如果提供的话）
    if args.date_expression:
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

    print("开始处理...")
    print(f"目录: {', '.join(args.dirs)}")
    print(f"GPX文件: {', '.join(args.gpx)}")
    print(
        f"日期表达式: {args.date_expression if args.date_expression else '无（不过滤）'}"
    )
    print(f"线程数: {threads}")
    if args.dry_run:
        print("模式: DRY RUN (不会修改文件)")
    print("-" * 50)

    # 创建并运行GeoTagger
    tagger = GeoTagger(args.dirs, args.gpx, args.date_expression, threads, args.dry_run)
    tagger.run()

    return 0


if __name__ == "__main__":
    exit(main())
