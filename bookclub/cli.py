"""指令列入口：`bookclub`。

`doctor`、`inspect`（10-02 第六批：安全查詢工作區）、`models download`、`run analyze`、`run turns`、`run execute`（10-01 起每一段開一支 `run part` 子程式）、`serve`、`ref`、`gen teacher`、`gen names`、
`gen students`、`render audio`、`render video`、`redo list`、`review export`／`review import`、`proofread prepare`
是真的會動的指令；`bench`、`export` 還沒做，執行會印出「哪個階段才會做」然後結束，讓還沒做完的
功能不會假裝成功，也不會讓人以為指令打錯了。`serve` 開的網頁裡，左側步驟列第 0～5 步都有頁面。
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

    inspect_parser = sub.add_parser(
        "inspect", help="安全查詢工作區：只印時間、編號、數字、狀態（給 AI 助手用，10-02 第六批）", add_help=False)
    inspect_parser.add_argument("rest", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)   # 交給 bookclub.safeview

    models_parser = sub.add_parser("models", help="模型相關指令")
    models_sub = models_parser.add_subparsers(dest="models_command")
    models_sub.add_parser("download", help="下載／確認三個模型都在該在的位置（已存在的檔案不重抓）")

    serve_parser = sub.add_parser("serve", help="開網頁伺服器")
    serve_parser.add_argument("workdir", nargs="?", help="工作區路徑（不給就在網頁總覽選影片、切換專案）")
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

    people_parser = run_sub.add_parser("people", help="找出這一集提到的所有人名（Claude 看整支逐字稿，名冊上沒有的也列出來）")
    people_parser.add_argument("workdir", help="工作區路徑（要先跑過 run analyze）")
    people_parser.add_argument("--force", action="store_true", help="已經有結果也重跑")

    cuts_parser = run_sub.add_parser("cuts", help="建議刪除段落（開頭空白、結尾道別、念聊天區留言、小組討論前後、技術問題）")
    cuts_parser.add_argument("workdir", help="工作區路徑（要先轉好文字）")
    cuts_parser.add_argument("--video", help="原片路徑（檢查畫面靜止用；預設讀分析結果記錄的影片）")
    cuts_parser.add_argument("--force", action="store_true", help="已經有結果也重跑")

    ex_parser = run_sub.add_parser("execute", help="流程第 4 步一次跑完：老師名字 → 學員重念 → 組裝（做過的跳過，可以中斷續跑）")
    ex_parser.add_argument("workdir", help="工作區路徑（要先跑過第 1 步轉文字、選定老師參考音）")
    ex_parser.add_argument("--start", help="從幾分幾秒（預設 0:00）")
    ex_parser.add_argument("--end", help="到幾分幾秒（預設影片結尾）")
    ex_parser.add_argument("--methods", help="組裝的輸出做法（逗號分隔：hw、sw、smart；預設 sw 標準輸出；hw 只有 Mac）")
    ex_parser.add_argument("--only", help="只跑這幾步（逗號分隔：老師名字,學員重念,組裝）")
    ex_parser.add_argument("--redo", action="store_true", help="做過的也重跑（生成本身還是會沿用快取，見 README）")
    ex_parser.add_argument("--redo-returned", action="store_true",
                           help="第 5 步退回的那幾筆一起重做：清掉那幾句重新生成，再重新組裝（網頁第 4 步按「開始執行」就是這樣）")

    part_parser = run_sub.add_parser(
        "part", help="第 4 步內部用（10-01）：「AI 執行」一支程式只跑一段、只載入一個模型；平常不用自己打，run execute 會開")
    part_parser.add_argument("workdir", help="工作區路徑")
    part_parser.add_argument("step", choices=["老師名字", "學員重念", "保留原聲學員名字", "組裝"], help="哪一步")
    part_parser.add_argument("phase", nargs="?", choices=["生成", "停頓", "收尾"],
                             help="生成＝只生成（生成模型）；停頓＝只插入停頓（對位模型）；收尾＝改語速重生成（要的話）＋放回時間格")
    part_parser.add_argument("--voice", help="學員重念：只做這個聲線（參考音檔路徑）")
    part_parser.add_argument("--who", help="保留原聲學員名字：只做這位學員（學員N）")
    part_parser.add_argument("--start", type=float, help="範圍開始（秒）")
    part_parser.add_argument("--end", type=float, help="範圍結束（秒）")
    part_parser.add_argument("--methods", help="組裝的輸出做法（逗號分隔）")
    part_parser.add_argument("--tag", help="組裝的輸出檔名標記")

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

    ref_recut_parser = ref_sub.add_parser(
        "recut", help="舊工作區的參考音候選照原本的時間重切成 48kHz（10-01，不重挑、不動已選定的 ref.wav）")
    ref_recut_parser.add_argument("workdir", help="工作區路徑")
    ref_recut_parser.add_argument("--video", help="原片路徑（預設用挑選紀錄.json 記的）")
    ref_recut_parser.add_argument("--out-subdir", default="參考音", help="參考音子資料夾名稱（預設「參考音」）")

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

    gen_sn = gen_sub.add_parser("stunames", help="保留原聲的學員講到名字、選了換成代號的：用他自己的聲音生成代號短句")
    gen_sn.add_argument("workdir", help="工作區路徑")
    gen_sn.add_argument("--no-check", action="store_true", help="不用 Groq 轉回文字檢查")
    gen_sn.add_argument("--no-pauses", action="store_true", help="不照原片停頓插入空白")

    gen_st = gen_sub.add_parser("students", help="學員段落用匿名聲線（男聲／女聲）重念（測試版）")
    gen_st.add_argument("workdir", help="工作區路徑（要先跑過 run analyze）")
    gen_st.add_argument("--start", help="從幾分幾秒開始（例如 37:00）")
    gen_st.add_argument("--end", help="到幾分幾秒（例如 55:23）")
    gen_st.add_argument("--only", help="只做這幾個學員段落（逗號分隔，例如 T038,T032）")
    gen_st.add_argument("--male", help="男生全部用這個參考音（測試用；預設每位學員各自的聲線，依男女輪流配 聲線/候選_0928/，逐字稿同檔名 .txt）")
    gen_st.add_argument("--female", help="女生全部用這個參考音（測試用）")
    gen_st.add_argument("--no-check", action="store_true", help="不用 Groq 轉回文字檢查")
    gen_st.add_argument("--no-pauses", action="store_true", help="不照原片停頓插入空白")
    gen_st.add_argument("--plan-only", action="store_true", help="只列出會生成哪幾段（不載入模型）")
    gen_st.add_argument("--include-kept", action="store_true", help="設成保留原聲的學員也照樣重念（測試聽生成效果用）")
    gen_st.add_argument("--redo", action="store_true", help="範圍內的段落重做（已經生成過的聲音從快取沿用，只重做插入停頓、放回時間格）")

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

    prof_parser = sub.add_parser("profile", help="第 0 步初始化設定：設定包匯出／匯入（給協作夥伴）")
    prof_sub = prof_parser.add_subparsers(dest="profile_command")
    pexp = prof_sub.add_parser("export", help="把名冊、敏感詞、名字排除清單、發音對照表、settings.toml 打包成一個 zip")
    pexp.add_argument("--out", help="zip 存到哪裡（預設 ~/讀書會剪輯資料/設定包/設定包_時間.zip）")
    pimp = prof_sub.add_parser("import", help="匯入設定包：第一欄當鑰匙，新的加進去、已經有的不動，內容不同列出衝突")
    pimp.add_argument("zip", help="設定包 zip")

    redo_parser = sub.add_parser("redo", help="第 5 步成品檢查退回的項目（第 4 步只重做這幾筆）")
    redo_sub = redo_parser.add_subparsers(dest="redo_command")
    redo_list = redo_sub.add_parser("list", help="列出要重做的項目（成品檢查按了「送回 AI 重做」的那一份）")
    redo_list.add_argument("workdir", help="工作區路徑")

    codes_parser = sub.add_parser("codes", help="這一集的代號（10-02 起用外國人名的中文寫法）")
    codes_sub = codes_parser.add_subparsers(dest="codes_command")
    cconv = codes_sub.add_parser("convert", help="把這一集的英文代號換成中文：存代號的地方與要念的文字一起換，先備份")
    cconv.add_argument("workdir", help="工作區路徑")
    cconv.add_argument("--map", action="append", metavar="舊=新",
                       help="對照，例如 --map Joan=潔西（可以給好幾次；舊名單裡有對應的不給也會照名單換）")
    cconv.add_argument("--dry-run", action="store_true", help="只列出會換幾處，不改檔")

    render_parser = sub.add_parser("render", help="組裝：把生成的聲音、消音放回原本的時間")
    render_sub = render_parser.add_subparsers(dest="render_command")
    render_audio_parser = render_sub.add_parser("audio", help="組出跟原片等長的新聲音軌＋處理前後試聽")
    render_audio_parser.add_argument("workdir", help="工作區路徑")
    render_audio_parser.add_argument("--video", help="原片影片路徑（預設讀逐字稿記錄的來源）")
    rv = render_sub.add_parser("video", help="組裝一段範圍的影片：換聲音＋刪除＋停格＋模糊示範（測試版）")
    rv.add_argument("workdir", help="工作區路徑")
    rv.add_argument("--start", required=True, help="從幾分幾秒（例如 37:00）")
    rv.add_argument("--end", required=True, help="到幾分幾秒（例如 55:23）")
    rv.add_argument("--video", help="原片影片路徑")
    rv.add_argument("--label", action="store_true", help="另外輸出標字試看版（AI 處理的時段左上角標字、下方字幕是餵給模型的文字）")
    rv.add_argument("--methods", default="sw", help="輸出做法（逗號分隔，預設 sw）：sw 整段軟體編碼、hw 硬體編碼（Mac）、smart 只重做有動到的片段")
    rv.add_argument("--include-kept", action="store_true", help="設成保留原聲的學員也照樣換聲音（測試用）")
    rv.add_argument("--tag", help="輸出檔名標記（預設「開始分-結束分」）")
    rv.add_argument("--no-demo-freeze", action="store_true", help="不做停格示範與模糊示範（正式成品 run execute 本來就不做）")

    return parser


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "doctor":
        from bookclub.doctor import main as doctor_main

        return doctor_main(args.rest)

    if args.command == "inspect":
        from bookclub.safeview import main as inspect_main

        return inspect_main(args.rest)

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
        if args.run_command == "people":
            from bookclub import personnames

            personnames.find_people(args.workdir, force=args.force)
            return 0
        if args.run_command == "cuts":
            from bookclub.cutsuggest import suggest_cuts

            suggest_cuts(args.workdir, video=args.video, force=args.force)
            return 0
        if args.run_command == "part":
            from bookclub.execute import run_part

            return run_part(args.workdir, args.step, args.phase, voice=args.voice, who=args.who, start=args.start,
                            end=args.end, tag=args.tag,
                            methods=[m.strip() for m in args.methods.split(",") if m.strip()] if args.methods else None)
        if args.run_command == "execute":
            from bookclub.execute import PartFailed, run_execute
            from bookclub.review import parse_time

            try:
                run_execute(args.workdir, start=parse_time(args.start) if args.start else None,
                            end=parse_time(args.end) if args.end else None,
                            methods=[m.strip() for m in args.methods.split(",") if m.strip()] if args.methods else None,
                            only_steps=[x.strip() for x in args.only.split(",") if x.strip()] if args.only else None,
                            redo=args.redo, redo_returned=args.redo_returned)
            except (FileNotFoundError, ValueError, PartFailed) as e:   # 前置檢查沒過、子程式出錯：印清楚就好，不印程式追蹤
                print(f"⚠️ {e}")
                return 1
            return 0
        if args.run_command == "turns":
            from bookclub.turns import build_turns

            build_turns(args.workdir, model=args.model)
            from bookclub import students as students_mod

            students_mod.estimate_pitches(args.workdir)   # 09-30：第 3 步就看得到配了哪個匿名聲線
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
            finalize_reference(args.workdir, args.rank, text, replace_audio=True)   # 10-01：指令列明確選定，一律換音檔
            return 0
        if args.ref_command == "recut":
            from bookclub.refpick import recut_candidates

            recut_candidates(args.workdir, video=args.video, ref_dir_name=args.out_subdir)
            return 0
        print("用法：bookclub ref pick <影片> <工作區> [--teacher-ref 檔案] [--n 5]")
        print("     bookclub ref use <工作區> <名次> --text-file <檔案>")
        print("     bookclub ref recut <工作區> [--video 原片]")
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
        if args.gen_command == "stunames":
            from bookclub import studentgen, studentnames

            sp = studentnames.plan(args.workdir)
            print(f"[保留原聲學員名字] 直接消音 {len(sp['消音'])} 筆、換成代號 {len(sp['生成'])} 句、要人處理 {len(sp['要人處理'])} 筆")
            studentgen.generate(args.workdir, check_content=not args.no_check, use_pauses=not args.no_pauses)
            return 0
        if args.gen_command == "students":
            from pathlib import Path as _Path

            from bookclub import students
            from bookclub.review import parse_time

            start = parse_time(args.start) if args.start else None
            end = parse_time(args.end) if args.end else None
            only = [x.strip() for x in args.only.split(",") if x.strip()] if args.only else None
            if args.plan_only:
                items, _ = students.build_items(_Path(args.workdir).expanduser(), start, end, only,
                                                include_kept=args.include_kept)
                for it in items:
                    print(f"{it['id']}\t{it['學員']}\t{it['slot'][0]:.1f}–{it['slot'][1]:.1f}\t{it['slot_s']:.1f} 秒\t"
                          f"{len(it['句子'])} 句\t換代號 {it['換成代號']}")
                print(f"共 {len(items)} 段、{sum(it['slot_s'] for it in items):.0f} 秒")
                return 0
            refs = {}   # 09-30：沒給 --male／--female 就每位學員各自的聲線（依男女輪流配）
            if args.male:
                refs["男"] = _Path(args.male).expanduser()
            if args.female:
                refs["女"] = _Path(args.female).expanduser()
            students.generate_students(args.workdir, start=start, end=end, only=only, refs=refs,
                                       check_content=not args.no_check, use_pauses=not args.no_pauses, redo=args.redo,
                                       include_kept=args.include_kept)
            return 0
        print("用法：bookclub gen teacher <工作區> <句子清單> [--ref-wav 檔案] [--ref-text 檔案] [--no-check] [--redo]")
        print("     bookclub gen students <工作區> [--start 37:00 --end 55:23] [--only T038] [--plan-only]")
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

    if args.command == "profile":
        from bookclub import profile

        if args.profile_command == "export":
            profile.export_profile(args.out)
            return 0
        if args.profile_command == "import":
            r = profile.import_profile(args.zip)
            for name, v in r["檔案"].items():
                print(f"  {name}：新增 {v['新增']} 筆" + (f"、衝突 {len(v['衝突'])} 筆（保留本機的）：" +
                      "、".join(str(c["鑰匙"]) for c in v["衝突"]) if v["衝突"] else ""))
            return 0
        print("用法：bookclub profile export [--out 檔案.zip]")
        print("     bookclub profile import <設定包.zip>")
        return 2

    if args.command == "redo":
        if args.redo_command == "list":
            from bookclub.finalcheck import redo_list
            from bookclub.workdir import fmt_time

            r = redo_list(args.workdir)
            if not r["項目"]:
                print("沒有要重做的項目（第 5 步成品檢查沒有退回的）。")
                return 0
            print(("已送回 AI 重做（" + r["時間"] + "）" if r["已送回"] else "還沒按「送回 AI 重做」，先列出目前退回的")
                  + f"：{len(r['項目'])} 筆")
            for i, it in enumerate(r["項目"], 1):
                t = fmt_time(it["原片"][0]) if it.get("原片") else "—"
                print(f"{i}. 原片 {t}　{it['類型']}　{('、'.join(it['覆核項目']) or '—')}　原因：{it['原因']}")
                print(f"   → {it['說明']}")
            print(f"一次重做這幾筆：{r['項目'][0]['建議指令']}（網頁第 4 步按「開始執行」也一樣）")
            return 0
        print("用法：bookclub redo list <工作區>")
        return 2

    if args.command == "codes":
        if args.codes_command == "convert":
            from bookclub import codeswap

            rows = codeswap.plan(args.workdir)["英文代號"]
            if not rows:
                print("這一集沒有英文代號了，不用換。")
                return 0
            print("這一集還在用的英文代號（舊 → 建議；存代號的地方幾格、要念的文字裡幾處）：")
            for r in rows:
                print(f"  {r['舊']} → {r['建議'] or '（舊名單沒有對應，要用 --map 給）'}；{r['存代號的地方']} 格、{r['要念的文字']} 處")
            try:
                res = codeswap.apply(args.workdir, codeswap.parse_map(args.map), dry_run=args.dry_run)
            except ValueError as e:
                print(f"沒有換：{e}")
                return 2
            print(("試跑（沒有改檔）：" if args.dry_run else "換好了：")
                  + "；".join(f"{o} → {n}（{res['每個代號換了幾處'][o]} 處）" for o, n in res["對照"].items()))
            if not args.dry_run:
                print(f"改到的檔：{'、'.join(res['改到的檔']) or '—'}；備份在工作區的 {res['備份']}")
                print("換過的句子到第 4 步會重新生成（做過的判斷照舊：文字對不上就重做）")
                if res.get("還剩"):
                    print(f"⚠️ 還有英文代號沒換到：{'、'.join(res['還剩'])}")
            return 0
        print("用法：bookclub codes convert <工作區> [--map Joan=潔西 ...] [--dry-run]")
        return 2

    if args.command == "render":
        if args.render_command == "audio":
            from bookclub.assemble import render_audio

            render_audio(args.workdir, video=args.video)
            return 0
        if args.render_command == "video":
            import json as _json

            from bookclub.render import render_video
            from bookclub.review import parse_time

            methods = [m.strip() for m in args.methods.split(",") if m.strip()]
            s = render_video(args.workdir, parse_time(args.start), parse_time(args.end), video=args.video,
                             label=args.label, methods=methods, tag=args.tag, demo_freeze=not args.no_demo_freeze,
                             demo_blur=not args.no_demo_freeze,
                             include_kept=args.include_kept)
            print(_json.dumps({k: v for k, v in s.items() if k != "警告"}, ensure_ascii=False, indent=1))
            return 0
        print("用法：bookclub render audio <工作區> [--video 影片]")
        print("     bookclub render video <工作區> --start 37:00 --end 55:23 [--label] [--methods hw,sw,smart]")
        return 2

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
