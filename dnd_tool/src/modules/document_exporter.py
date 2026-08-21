"""完整扩写版文档整合：按章节顺序合并所有已采纳版本为 Markdown。

文件保存到 outputs/ 目录，命名为 {模组名}_expanded.md，
开头包含元数据：原始文件名、扩写日期、AI 模型。
"""
from datetime import datetime
from pathlib import Path

from src.modules.database import Database

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
OUTPUT_DIR = PROJECT_ROOT / "outputs"


def configure_output_dir(path: str | Path) -> None:
    """设置当前登录账户的默认导出目录。"""
    global OUTPUT_DIR
    OUTPUT_DIR = Path(path)


def _safe_stem(file_name: str) -> str:
    """去掉扩展名的模组名（清理非法文件名字符）。"""
    stem = Path(file_name).stem or "module"
    for ch in r'\/:*?"<>|':
        stem = stem.replace(ch, "_")
    return stem


def export_expanded_document(
    db: Database,
    file_name: str,
    model: str = "",
    output_dir: str | Path | None = None,
) -> Path:
    """把某模组的全部已采纳扩写版本导出为 Markdown。

    未扩写（keep）或没有版本的段落保留原文；按章节顺序输出。
    :return: 生成的 Markdown 文件路径
    """
    out_dir = Path(output_dir) if output_dir else OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    segments = db.get_segments(file_name)
    selected = {
        seg["id"]: db.get_selected_version(seg["id"])
        for seg in segments
    }

    lines: list[str] = []
    lines.append(f"# {_safe_stem(file_name)}（扩写版）\n")
    lines.append(f"- 原始文件：{file_name}")
    lines.append(f"- 扩写日期：{datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"- AI 模型：{model or '未知'}")
    lines.append("")

    current_chapter = None
    for seg in segments:
        chapter = seg.get("chapter", "未分章")
        if chapter != current_chapter:
            current_chapter = chapter
            lines.append("")
            lines.append(f"## {chapter}")
            lines.append("")
        expanded = selected.get(seg["id"])
        content = expanded if expanded else seg.get("content", "")
        lines.append(content)
        lines.append("")

    out_path = out_dir / f"{_safe_stem(file_name)}_expanded.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path
