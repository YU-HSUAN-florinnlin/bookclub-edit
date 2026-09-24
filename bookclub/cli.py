"""指令列入口：`bookclub`。

`doctor`、`models download`、`run analyze`、`serve` 是真的會動的指令；
`bench`、`export` 還沒做，執行會印出「哪個階段才會做」然後結束，讓還沒做完的
功能不會假裝成功，也不會讓人以為指令打錯了。`serve` 開的網頁裡，左側步驟列
也只有第 1、2、4 步是真的（對應 `run analyze` 的轉文字／挑參考音／找名字），
其餘步驟頁面同樣只顯示「還沒做」。
"""

from __future__ import annotations

import argparse
import sys


def _not_yet(step_name: str, phase: int) -> int:
    print(f"「{step_name}」這一步在第 {phase} 階段才會做，目前是 Phase 0（骨架與環境），還沒做這個功能。")
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bookclub", description="讀書會影片隱私處理工具")
    sub = parser.add_subparsers(dest="command")

    doctor_parser = sub.add_parser("doctor", help="檢查環境是否就緒", add_help=False)
    doctor_parser.add_argument(
        "rest", nargs=argparse.REMAINDER, help=argparse.SUPPRESS,
    )  # --smoke／--claude／--help 都交給 bookclub.doctor 自己的 argparse 處理

    models_parser = sub.add_parser("models", help="模型相關指令")
    models_sub = models_parser.add_subparsers(dest="models_command")
    models_sub.add_parser("download", help="下載／確認三個模型都在該在的位置（已存在的檔案不重抓）")

    serve_parser = sub.add_parser("serve", help="開網頁伺服器")
    serve_parser.add_argument("workdir", help="工作區路徑")
    serve_parser.add_argument("--video", help="直接帶入影片路徑（按「開始分析」時要用到）")
    serve_parser.add_argument("--port", type=int, help="伺服器埠號（預設讀 settings.toml）")
    serve_parser.add_argument("--no-open", action="store_true", help="啟動後不要自動開瀏覽器")

    bench_parser = sub.add_parser("bench", help="印出各步驟耗時表（Phase 5 才會做）")
    bench_parser.add_argument("workdir", nargs="?", help="工作區路徑")

    run_parser = sub.add_parser("run", help="終端機單跑某一步，排錯用")
    run_sub = run_parser.add_subparsers(dest="run_command")

    analyze_parser = run_sub.add_parser(
        "analyze", help="流程第 1 步：轉文字→認老師→找重疊→挑參考音→找名字，一次跑完"
    )
    analyze_parser.add_argument("video", help="影片檔路徑")
    analyze_parser.add_argument("workdir", help="工作區路徑")
    analyze_parser.add_argument("--roster", help="名冊 CSV 路徑（不給就跳過找名字）")
    analyze_parser.add_argument("--sensitive", help="敏感詞 CSV 路徑")
    analyze_parser.add_argument(
        "--exclusions",
        help="名字排除清單 CSV 路徑（不給就預設抓 --roster 同一層目錄下的「名字排除清單.csv」，不存在就當作沒有）",
    )
    analyze_parser.add_argument("--skip-overlap", action="store_true", help="跳過找重疊（排錯、省時間用）")
    analyze_parser.add_argument("--ref-n", type=int, default=5, help="挑幾個老師參考音候選（預設 5）")

    sub.add_parser("export", help="匯出成品（Phase 5 才會做）")

    ref_parser = sub.add_parser("ref", help="老師參考音相關指令")
    ref_sub = ref_parser.add_subparsers(dest="ref_command")

    ref_pick_parser = ref_sub.add_parser("pick", help="從影片自動挑老師參考音")
    ref_pick_parser.add_argument("video", help="影片檔路徑")
    ref_pick_parser.add_argument("workdir", help="工作區路徑（輸出放在 workdir/參考音/）")
    ref_pick_parser.add_argument("--teacher-ref", help="老師自錄音檔路徑，只用來確認判斷，不參與判斷")
    ref_pick_parser.add_argument("--n", type=int, default=5, help="推薦幾個候選（預設 5）")
    ref_pick_parser.add_argument(
        "--window-scan", action="store_true",
        help="打開步驟 5c 逐窗細看聲紋（預設關閉，改靠排除區域擋混到別人聲音的候選）",
    )
    ref_pick_parser.add_argument(
        "--force-combine", action="store_true",
        help="不管單一段落候選夠不夠，強制只產拼接候選（測試用）",
    )
    ref_pick_parser.add_argument(
        "--out-subdir", default="參考音",
        help="輸出子資料夾名稱（預設「參考音」）；抽音／轉文字／找老師的共用快取還是放在工作區底下，不受影響",
    )

    ref_use_parser = ref_sub.add_parser("use", help="選定某一名次的候選，存成 ref.wav／ref.txt")
    ref_use_parser.add_argument("workdir", help="工作區路徑")
    ref_use_parser.add_argument("rank", type=int, help="名次（第幾段）")
    ref_use_parser.add_argument("--text-file", required=True, help="修正好的逐字稿檔案路徑")

    return parser


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "doctor":
        from bookclub.doctor import main as doctor_main

        return doctor_main(args.rest)

    if args.command == "models":
        if args.models_command == "download":
            from bookclub.models import download_all

            download_all()
            return 0
        print("用法：bookclub models download")
        return 2

    if args.command == "serve":
        from bookclub.server import serve

        serve(args.workdir, video=args.video, port=args.port, open_browser=not args.no_open)
        return 0

    if args.command == "bench":
        return _not_yet("bench", 5)

    if args.command == "run":
        if args.run_command == "analyze":
            from bookclub.analyze import run_analyze

            run_analyze(
                args.video, args.workdir, roster_path=args.roster, sensitive_path=args.sensitive,
                exclusion_path=args.exclusions,
                skip_overlap=args.skip_overlap, ref_n=args.ref_n,
            )
            return 0
        print("用法：bookclub run analyze <影片> <工作區> [--roster 名冊.csv] [--sensitive 敏感詞.csv] "
              "[--exclusions 名字排除清單.csv] [--skip-overlap]")
        return 2

    if args.command == "export":
        return _not_yet("export", 5)

    if args.command == "ref":
        if args.ref_command == "pick":
            from bookclub.refpick import pick_reference

            pick_reference(
                args.video, args.workdir, n=args.n, teacher_ref=args.teacher_ref,
                window_scan=args.window_scan, force_combine=args.force_combine,
                ref_dir_name=args.out_subdir,
            )
            return 0
        if args.ref_command == "use":
            from pathlib import Path as _Path

            from bookclub.refpick import finalize_reference

            text = _Path(args.text_file).read_text(encoding="utf-8")
            finalize_reference(args.workdir, args.rank, text)
            return 0
        print("用法：bookclub ref pick <影片> <工作區> [--teacher-ref 檔案] [--n 5]")
        print("     bookclub ref use <工作區> <名次> --text-file <檔案>")
        return 2

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
