"""勉強用教材/*.kif を読んで、HTML 練習ビューア用 JSON を生成する。

使い方:
  python make_study_kifu.py

入力: 勉強用教材/001.kif 〜 010.kif (CP932)
出力: study_kifu.generated.json (UTF-8)

各 KIF は局面図 (`+---...+` で囲まれた 9x9) + 持駒 + 「後手番」+ 指し手で構成。
やばボーズ流は常に後手 = 振り飛車側 (ユーザー操作)。
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

import shogi

HERE = Path(__file__).resolve().parent
KIF_DIR = HERE / "勉強用教材"
OUT_JSON = HERE / "study_kifu.generated.json"
OUT_JS = HERE / "study_kifu.generated.js"

ZEN_DIGIT = "０１２３４５６７８９"
KAN_DIGIT = "〇一二三四五六七八九"
RANK_LETTER = "abcdefghi"

DROP_PIECE_USI = {
    "歩": "P", "香": "L", "桂": "N", "銀": "S",
    "金": "G", "角": "B", "飛": "R",
}

# 局面図の駒記号 → (SFEN 基本文字 (大文字), 元から成駒か)
DIAGRAM_PIECE: dict[str, tuple[str, bool]] = {
    "歩": ("P", False), "香": ("L", False), "桂": ("N", False),
    "銀": ("S", False), "金": ("G", False),
    "角": ("B", False), "飛": ("R", False),
    "玉": ("K", False), "王": ("K", False),
    "と": ("P", True), "杏": ("L", True), "圭": ("N", True), "全": ("S", True),
    "馬": ("B", True), "龍": ("R", True), "竜": ("R", True),
}

KAN_COUNT = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
    "十": 10, "十一": 11, "十二": 12, "十三": 13, "十四": 14, "十五": 15,
    "十六": 16, "十七": 17, "十八": 18,
}

HAND_ORDER = ["R", "B", "G", "S", "N", "L", "P"]

TERMINATION = {
    "投了", "中断", "詰み", "持将棋", "千日手",
    "切れ負け", "反則勝ち", "反則負け", "入玉宣言", "封じ手",
}
TIME_RE = re.compile(r"\s*\(\s*\d+:\d+\s*/\s*\d+:\d+:\d+\s*\)\s*$")
MOVE_LINE_RE = re.compile(r"^\s*(\d+)\s+(.*)$")
RANK_LINE_RE = re.compile(r"^\|(.+?)\|([一二三四五六七八九])$")


def read_kif(path: Path) -> tuple[str, str]:
    for enc in ("utf-8-sig", "utf-8", "cp932"):
        try:
            return path.read_text(encoding=enc), enc
        except UnicodeDecodeError:
            continue
    raise RuntimeError(f"failed to decode {path}")


def diagram_to_sfen_piece(piece_ch: str, promoted_prefix: bool, is_gote: bool) -> str:
    base, inherent = DIAGRAM_PIECE[piece_ch]
    is_promoted = promoted_prefix or inherent
    letter = base.lower() if is_gote else base
    return ("+" + letter) if is_promoted else letter


def parse_rank_cells(body: str) -> list[str]:
    """1 ランク分のセル列。空セルは '' を入れる。"""
    cells: list[str] = []
    i = 0
    L = len(body)
    while i < L and len(cells) < 9:
        c = body[i]
        if c == "v":
            is_gote = True
            i += 1
        elif c == " " or c == "　":
            is_gote = False
            i += 1
        else:
            i += 1
            continue
        if i >= L:
            break
        c2 = body[i]
        if c2 == "・":
            cells.append("")
            i += 1
        elif c2 == "成":
            if i + 1 >= L:
                break
            piece_ch = body[i + 1]
            cells.append(diagram_to_sfen_piece(piece_ch, True, is_gote))
            i += 2
        else:
            cells.append(diagram_to_sfen_piece(c2, False, is_gote))
            i += 1
    while len(cells) < 9:
        cells.append("")
    return cells


def parse_hand_value(val: str) -> dict[str, int]:
    """`角` `銀 歩` `歩二 金 角` などを {駒大文字: 個数} に。"""
    counts: dict[str, int] = {}
    for tok in re.split(r"\s+", val.strip()):
        if not tok:
            continue
        piece_ch = tok[0]
        rest = tok[1:]
        base, _ = DIAGRAM_PIECE.get(piece_ch, ("", False))
        if not base:
            continue
        count = KAN_COUNT.get(rest, 1) if rest else 1
        counts[base] = counts.get(base, 0) + count
    return counts


def normalize_hand(counts: dict[str, int], is_gote: bool) -> str:
    """SFEN 形式の持駒文字列を生成。R, B, G, S, N, L, P 順。"""
    result = ""
    for base in HAND_ORDER:
        c = counts.get(base, 0)
        if c == 0:
            continue
        letter = base.lower() if is_gote else base
        if c == 1:
            result += letter
        else:
            result += str(c) + letter
    return result


def parse_board_diagram(text: str) -> tuple[str, str]:
    """KIF テキスト → (sfen, turn)."""
    lines = text.splitlines()

    rank_rows: list[list[str]] = []
    for line in lines:
        s = line.rstrip()
        m = RANK_LINE_RE.match(s)
        if m:
            rank_rows.append(parse_rank_cells(m.group(1)))
    if len(rank_rows) != 9:
        raise RuntimeError(f"expected 9 rank rows, got {len(rank_rows)}")

    sfen_ranks: list[str] = []
    for cells in rank_rows:
        s = ""
        empty = 0
        for cell in cells:
            if cell == "":
                empty += 1
            else:
                if empty:
                    s += str(empty)
                    empty = 0
                s += cell
        if empty:
            s += str(empty)
        sfen_ranks.append(s)
    sfen_board = "/".join(sfen_ranks)

    sente_counts: dict[str, int] = {}
    gote_counts: dict[str, int] = {}
    for line in lines:
        s = line.strip()
        sep = "：" if "：" in s else (":" if ":" in s else "")
        if not sep:
            continue
        if s.startswith("先手の持駒"):
            val = s.split(sep, 1)[1].strip()
            if val and val != "なし":
                sente_counts = parse_hand_value(val)
        elif s.startswith("後手の持駒"):
            val = s.split(sep, 1)[1].strip()
            if val and val != "なし":
                gote_counts = parse_hand_value(val)

    hand_str = normalize_hand(sente_counts, False) + normalize_hand(gote_counts, True)
    if not hand_str:
        hand_str = "-"

    turn = "b"
    for line in lines:
        s = line.strip()
        if s == "後手番":
            turn = "w"
            break
        if s == "先手番":
            turn = "b"
            break

    sfen = f"{sfen_board} {turn} {hand_str} 1"
    return sfen, turn


def parse_jp_move(text: str, last_dst: tuple[int, int] | None
                  ) -> tuple[str, int, int, str] | None:
    """KIF の指し手テキスト → (usi, dst_file, dst_rank, display_jp)."""
    text = text.strip()
    if not text or text in TERMINATION:
        return None

    if text.endswith("打"):
        if len(text) < 3:
            return None
        file_ch = text[0]
        rank_ch = text[1]
        piece = text[2:-1]
        if file_ch not in ZEN_DIGIT or rank_ch not in KAN_DIGIT:
            return None
        if piece not in DROP_PIECE_USI:
            return None
        file = ZEN_DIGIT.index(file_ch)
        rank = KAN_DIGIT.index(rank_ch)
        usi = f"{DROP_PIECE_USI[piece]}*{file}{RANK_LETTER[rank - 1]}"
        return (usi, file, rank, text)

    if "(" in text and text.endswith(")"):
        body, src = text.rsplit("(", 1)
        src = src.rstrip(")")
        if len(src) != 2 or not src.isdigit():
            return None
        src_file = int(src[0])
        src_rank = int(src[1])

        if body.startswith("同"):
            if last_dst is None:
                return None
            file, rank = last_dst
            piece_part = body[1:].lstrip("　 ")
        else:
            if len(body) < 3:
                return None
            if body[0] not in ZEN_DIGIT or body[1] not in KAN_DIGIT:
                return None
            file = ZEN_DIGIT.index(body[0])
            rank = KAN_DIGIT.index(body[1])
            piece_part = body[2:]

        promoted = piece_part.endswith("成") and not (
            piece_part.startswith("成") and len(piece_part) > 1
        )
        usi = f"{src_file}{RANK_LETTER[src_rank - 1]}{file}{RANK_LETTER[rank - 1]}"
        if promoted:
            usi += "+"
        return (usi, file, rank, text)

    return None


def parse_kif_moves(text: str) -> tuple[list[dict], list[str]]:
    """指し手 + 「最初の手より前」のコメントを抽出。"""
    moves: list[dict] = []
    head_comments: list[str] = []
    in_moves = False
    last_dst: tuple[int, int] | None = None
    last_idx: int | None = None

    for raw in text.splitlines():
        s = raw.strip()
        if not in_moves:
            if s.startswith("手数----"):
                in_moves = True
            continue
        if not s:
            continue
        if s.startswith("**"):
            continue
        if s.startswith("*"):
            comment = s[1:].strip()
            if last_idx is None:
                if comment:
                    head_comments.append(comment)
            else:
                if comment:
                    moves[last_idx].setdefault("comments", []).append(comment)
            continue
        if s.startswith("まで") or s.startswith("変化"):
            break

        m = MOVE_LINE_RE.match(raw.rstrip())
        if not m:
            continue
        rest = TIME_RE.sub("", m.group(2)).strip()
        if not rest:
            continue
        parsed = parse_jp_move(rest, last_dst)
        if parsed is None:
            continue
        usi, dst_file, dst_rank, jp = parsed
        display_jp = re.sub(r"\([1-9][1-9]\)$", "", jp)
        moves.append({
            "ply": int(m.group(1)),
            "usi": usi,
            "notation": display_jp,
            "dst": (dst_file, dst_rank),
        })
        last_idx = len(moves) - 1
        last_dst = (dst_file, dst_rank)
    return moves, head_comments


def process_kif(path: Path) -> dict:
    text, enc = read_kif(path)
    sfen, turn = parse_board_diagram(text)
    moves, head_comments = parse_kif_moves(text)

    board = shogi.Board(sfen)
    validated: list[str] = []
    sfen_history: list[str] = [board.sfen()]
    for mv in moves:
        try:
            move = shogi.Move.from_usi(mv["usi"])
        except Exception as e:
            raise RuntimeError(f"USI parse error ply {mv['ply']} {mv['usi']}: {e}")
        if move not in board.legal_moves:
            raise RuntimeError(f"illegal move ply {mv['ply']} usi={mv['usi']} notation={mv['notation']}")
        board.push(move)
        validated.append(mv["usi"])
        sfen_history.append(board.sfen())

    move_meta = []
    for i, mv in enumerate(moves):
        move_meta.append({
            "ply": mv["ply"],
            "side": "gote" if (turn == "w") == (i % 2 == 0) else "sente",
            "usi": mv["usi"],
            "notation": mv["notation"],
            "comments": mv.get("comments", []),
        })

    return {
        "id": path.stem,
        "label": f"勉強用教材 {path.stem}",
        "encoding": enc,
        "start_sfen": sfen,
        "user_side": "gote" if turn == "w" else "sente",
        "kifu_moves": validated,
        "kifu_moves_ja": [mv["notation"] for mv in moves],
        "move_meta": move_meta,
        "sfen_history": sfen_history,
        "head_comments": head_comments,
    }


def main():
    if not KIF_DIR.is_dir():
        print(f"NOT FOUND: {KIF_DIR}", file=sys.stderr)
        sys.exit(1)
    paths = sorted(KIF_DIR.glob("*.kif"))
    if not paths:
        print(f"no .kif files in {KIF_DIR}", file=sys.stderr)
        sys.exit(1)

    problems = []
    for p in paths:
        try:
            prob = process_kif(p)
        except Exception as e:
            print(f"  ERROR {p.name}: {e}", file=sys.stderr)
            raise
        marker = "" if prob["user_side"] == "gote" else " ⚠ user_side != gote"
        print(f"  {p.name}: {len(prob['kifu_moves'])} moves{marker}")
        problems.append(prob)

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_dir": KIF_DIR.name,
        "problems": problems,
    }
    json_text = json.dumps(payload, ensure_ascii=False, indent=2)
    OUT_JSON.write_text(json_text, encoding="utf-8")
    print(f"wrote {OUT_JSON} ({OUT_JSON.stat().st_size} bytes, {len(problems)} problems)")

    js_text = f"window.STUDY_DATA = {json_text};\n"
    OUT_JS.write_text(js_text, encoding="utf-8")
    print(f"wrote {OUT_JS} ({OUT_JS.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
