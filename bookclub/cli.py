"""指令列入口：`bookclub`。

`doctor`、`models download`、`run analyze`、`run turns`、`serve`、`ref`、`gen teacher`、`gen names`、`render audio`、
`review export`／`review import`、`proofread prepare` 是真的會動的指令；
`bench`、`export` 還沒做，執行會印出「哪個階段才會做」然後結束，讓還沒做完的
功能不會假裝成功，也不會讓人以為指令打錯了。`serve` 開的網頁裡，左側步驟列
第 1、2、3 步是真的（轉文字與分析、聲音分群與參考音、覆核工作台），其餘步驟頁面只顯示「還沒做」。
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
    analyze_parser.add_argument("--skip-turns", action="store_true", help="跳過段落分析（不呼叫 Claude）")

    turns_parser = run_sub.add_parser("turns", help="段落分析：看文字切段落、看聲音認人（第 3 步校對用）")
    turns_parser.add_argument("workdir", help="工作區路徑（要先跑過 run analyze）")
    turns_parser.add_argument("--model", help="claude -p 用的模型（預設讀 settings.toml 的 claude_models.turns）")

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

    gen_parser = sub.add_parser("gen", help="流程第 5 步：生成 AI 聲音")
    gen_sub = gen_parser.add_subparsers(dest="gen_command")
    gen_teacher_parser = gen_sub.add_parser("teacher", help="用老師的 AI 聲音重念指定句子")
    gen_teacher_parser.add_argument("workdir", help="工作區路徑（參考音預設讀 workdir/參考音/ref.wav、ref.txt）")
    gen_teacher_parser.add_argument(
        "sentences", help="句子清單：.txt 一行一句，或 .json（可附原片時間格 slot，見 bookclub/tts.py）"
    )
    gen_teacher_parser.add_argument("--ref-wav", help="改用別的參考音檔")
    gen_teacher_parser.add_argument("--ref-text", help="改用別的參考音逐字稿檔案")
    gen_teacher_parser.add_argument("--no-check", action="store_true", help="不用 Groq 轉回文字檢查（省時間、沒網路時用）")
    gen_teacher_parser.add_argument("--no-similarity", action="store_true", help="不算聲紋相似度")
    gen_teacher_parser.add_argument("--no-pauses", action="store_true", help="不照原片停頓插入空白（不載入逐字對位模型）")
    gen_teacher_parser.add_argument("--redo", action="store_true", help="忽略上次結果，全部重新生成")
    gen_teacher_parser.add_argument(
        "--pron-table", help="發音對照表 CSV（預設 ~/讀書會剪輯資料/發音對照表.csv，不存在就不換）"
    )

    gen_names_parser = gen_sub.add_parser("names", help="老師提到學員名字的地方：排出處理計畫、用老師 AI 聲音生成")
    gen_names_parser.add_argument("workdir", help="工作區路徑（要先跑過 run analyze、選定參考音）")
    gen_names_parser.add_argument("--only", help="只處理這幾筆候選（逗號分隔的編號，測試用），例如 1,5,9")
    gen_names_parser.add_argument("--plan-only", action="store_true", help="只排計畫、不生成")
    gen_names_parser.add_argument("--redo", action="store_true", help="忽略上次生成結果，全部重新生成")

    pr_parser = sub.add_parser("proofread", help="第 3 步：學員逐字稿校對")
    pr_sub = pr_parser.add_subparsers(dest="pr_command")
    pr_prep = pr_sub.add_parser("prepare", help="整理一段時間內學員說的句子，給校對頁用")
    pr_prep.add_argument("workdir", help="工作區路徑")
    pr_prep.add_argument("--start", default="0:00", help="從幾分幾秒開始（例如 43:15 或 1:05:00）")
    pr_prep.add_argument("--minutes", type=float, help="處理幾分鐘（不給就到影片結尾）")

    review_parser = sub.add_parser("review", help="第 3 步覆核結果：匯出給夥伴、在夥伴的電腦匯入")
    review_sub = review_parser.add_subparsers(dest="review_command")
    rexp = review_sub.add_parser("export", help="把覆核結果打包成一個 zip（網頁第 3 步也有按鈕）")
    rexp.add_argument("workdir", help="工作區路徑")
    rexp.add_argument("--out", help="zip 存到哪裡（預設 工作區/匯出/覆核結果_影片名_時間.zip）")
    rexp.add_argument("--video", help="原片路徑（預設讀分析結果記錄的影片，用來記下長度與大小）")
    rimp = review_sub.add_parser("import", help="匯入覆核結果：建工作區、抽聲音，接著就能 gen names、render audio")
    rimp.add_argument("zip", help="匯出的 zip")
    rimp.add_argument("workdir", help="新的工作區資料夾")
    rimp.add_argument("--video", required=True, help="原片路徑（要跟匯出時同一支，會比對長度）")
    rimp.add_argument("--force", action="store_true", help="影片長度對不上也照樣匯入")

    render_parser = sub.add_parser("render", help="組裝：把生成的聲音、消音放回原本的時間")
    render_sub = render_parser.add_subparsers(dest="render_command")
    render_audio_parser = render_sub.add_parser("audio", help="組出跟原片等長的新聲音軌＋處理前後試聽")
    render_audio_parser.add_argument("workdir", help="工作區路徑")
    render_audio_parser.add_argument("--video", help="原片影片路徑（預設讀逐字稿記錄的來源）")

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
                skip_overlap=args.skip_overlap, ref_n=args.ref_n, skip_turns=args.skip_turns,
            )
            return 0
        if args.run_command == "turns":
            from bookclub.turns import build_turns

            build_turns(args.workdir, model=args.model)
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

    if args.command == "gen":
        if args.gen_command == "teacher":
            from bookclub.tts import generate_teacher

            generate_teacher(
                args.workdir, args.sentences, ref_wav=args.ref_wav, ref_text_path=args.ref_text,
                check_content=not args.no_check, check_similarity=not args.no_similarity,
                use_pauses=not args.no_pauses, redo=args.redo, pron_table=args.pron_table,
            )
            return 0
        if args.gen_command == "names":
            from bookclub.nameplan import make_plan, sentences_path
            from bookclub.tts import generate_teacher

            only = [int(x) for x in args.only.split(",") if x.strip()] if args.only else None
            plan = make_plan(args.workdir, only=only)
            if plan["要人處理"]:
                print("要人處理的候選：" + "、".join(f"{m['候選']}（{m['原因']}）" for m in plan["要人處理"]))
            if plan["生成"] and not args.plan_only:
                from pathlib import Path as _Path

                generate_teacher(args.workdir, sentences_path(_Path(args.workdir).expanduser()), redo=args.redo)
            print("下一步：bookclub render audio <工作區>")
            return 0
        print("用法：bookclub gen teacher <工作區> <句子清單> [--ref-wav 檔案] [--ref-text 檔案] [--no-check] [--redo]")
        print("     bookclub gen names <工作區> [--only 1,5,9] [--plan-only]")
        return 2

    if args.command == "proofread":
        if args.pr_command == "prepare":
            from bookclub.proofread import build_sample

            parts = [float(x) for x in args.start.split(":")]
            start = sum(v * 60 ** k for k, v in enumerate(reversed(parts)))
            end = start + args.minutes * 60 if args.minutes else 1e9
            build_sample(args.workdir, start, end)
            print("下一步：bookclub serve <工作區>，打開第 3 步")
            return 0
        print("用法：bookclub proofread prepare <工作區> [--start 43:15] [--minutes 5]")
        return 2

    if args.command == "review":
        if args.review_command == "export":
            from bookclub.exchange import export_review

            export_review(args.workdir, out=args.out, video=args.video)
            return 0
        if args.review_command == "import":
            from bookclub.exchange import import_review

            import_review(args.zip, args.workdir, args.video, force=args.force)
            return 0
        print("用法：bookclub review export <工作區> [--out 檔案.zip]")
        print("     bookclub review import <zip> <新工作區> --video <影片> [--force]")
        return 2

    if args.command == "render":
        if args.render_command == "audio":
            from bookclub.assemble import render_audio

            render_audio(args.workdir, video=args.video)
            return 0
        print("用法：bookclub render audio <工作區> [--video 影片]")
        return 2

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
