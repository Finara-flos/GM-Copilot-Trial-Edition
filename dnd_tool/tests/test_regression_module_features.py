"""离线回归：扩写展示、模组词库隔离、NPC 与翻译分析响应解析。"""
import asyncio
import os
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from src.modules.database import Database
from src.modules.metaphor_translator import check_metaphors
from src.modules.npc_extractor import scan_and_build_all
from src.modules.term_checker import check_terms
from src.pages.import_page import ImportPage
from src.pages.npc_page import NpcPage
from src.widgets.comparison_view import ComparisonView


class FakeClient:
    """按提示词返回可覆盖三条分析路径的伪模型客户端。"""

    async def chat(self, messages, **_kwargs):
        prompt = "\n".join(item.get("content", "") for item in messages)
        if "提取所有 NPC" in prompt:
            return '模型结果： {"items": [{"name": "哈维尔"}, {"name": "酒馆老板"}]}'
        if "术语审查员" in prompt:
            return '```json\n{"groups": [{"concept": "精灵", "variants": ["精灵", "艾尔夫"]}]}\n```'
        if "文化转译者" in prompt:
            return '说明： {"results": [{"original": "Achilles heel", "suggestion": "阿喀琉斯之踵"}]}'
        if "NPC 档案生成助手" in prompt:
            return ('{"motivation": "守护村庄", "secret": "藏有密信", '
                    '"catchphrase": "请小心", "flaw": "多疑", '
                    '"appearance": "身着斗篷", "backstory": "长期居住于此"}')
        raise AssertionError(f"Unexpected prompt: {prompt[:100]}")


def main():
    """运行所有无网络回归断言。"""
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as temp_dir:
        db = Database(os.path.join(temp_dir, "test.db"))
        db.insert_segments([
            {"chapter": "第一章", "content": "哈维尔遇见艾尔夫。Achilles heel。"},
        ], "alpha.md")
        db.insert_segments([
            {"chapter": "第二章", "content": "另一份模组。"},
        ], "beta.md")
        segment = db.get_segments("alpha.md")[0]
        db.save_expanded_versions(segment["id"], ["这是保存的扩写版本。"])

        db.add_keyword("哈维尔", "npc", "村庄向导", "alpha.md")
        db.add_keyword("贝塔港", "place", "另一处地点", "beta.md")
        assert len(db.get_keywords("alpha.md")) == 1
        assert len(db.get_keywords("beta.md")) == 1

        page = ImportPage(db)
        page.set_current_file("alpha.md")
        assert page._keyword_file_combo.currentData() == "alpha.md"
        assert page._keyword_list.count() == 1
        page._keyword_file_combo.setCurrentIndex(page._keyword_file_combo.findData("beta.md"))
        assert page._keyword_list.count() == 1
        assert page._keyword_list.item(0).text().startswith("贝塔港")

        view = ComparisonView(db)
        view.load_segments(db.get_segments("alpha.md"), "alpha.md")
        assert "保存的扩写版本" in view._right_view.toPlainText()
        clicked = []
        view._segment_widgets[0].clicked.connect(lambda *args: clicked.append(args))
        view._segment_widgets[0]._body.content_clicked.emit()
        assert clicked and clicked[0][0] == segment["id"]
        view._on_segment_clicked(segment["id"], segment["chapter"], segment["content"])
        assert "保存的扩写版本" in view._right_view.toPlainText()

        client = FakeClient()
        npcs = asyncio.run(scan_and_build_all(client, "哈维尔和酒馆老板讨论精灵。", db, "alpha.md"))
        assert len(npcs) == 2
        assert len(db.get_npcs("alpha.md")) == 2
        npc_page = NpcPage(db)
        npc_page.set_current_file("alpha.md")
        assert npc_page._selected is None
        assert all(not button.property("selected") for button in npc_page._npc_list._buttons)
        npc_page._on_npc_selected("哈维尔")
        assert npc_page._selected["name"] == "哈维尔"
        assert next(button for button in npc_page._npc_list._buttons if button.text() == "哈维尔").property("selected")
        terms = asyncio.run(check_terms(client, "精灵与艾尔夫都出现。"))
        assert terms == [{"concept": "精灵", "variants": ["精灵", "艾尔夫"]}]
        metaphors = asyncio.run(check_metaphors(client, "Achilles heel"))
        assert metaphors == [{"original": "Achilles heel", "suggestion": "阿喀琉斯之踵"}]

        db.save_npc_dialogue("哈维尔", "alpha", "alpha 台词")
        db.upsert_npc({"name": "贝塔"}, "beta.md")
        db.save_npc_dialogue("贝塔", "beta", "beta 台词")
        db.clear_file("alpha.md")
        assert not db.get_segments("alpha.md")
        assert not db.get_keywords("alpha.md")
        assert not db.get_npcs("alpha.md")
        assert db.get_npc_dialogues("贝塔")[0]["line"] == "beta 台词"
        assert db.get_keywords("beta.md")[0]["detail"] == "另一处地点"
        db.close()
    app.processEvents()
    print("REGRESSION_OFFSCREEN_OK")


if __name__ == "__main__":
    main()
