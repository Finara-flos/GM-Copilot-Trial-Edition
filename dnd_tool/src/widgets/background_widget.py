"""窗口背景容器：绘制背景图（cover/tile/stretch）与半透明遮罩。

供 MainWindow 与 AccountDialog 共用，保证启动登录界面与主窗口背景视觉一致。
"""
from pathlib import Path

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QPainter, QPaintEvent, QPixmap
from PySide6.QtWidgets import QWidget


class BackgroundWidget(QWidget):
    """窗口背景容器：绘制背景图（cover/tile/stretch）与半透明遮罩。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("BackgroundWidget")
        self._pixmap: QPixmap | None = None
        self._mode = "cover"
        self._base_color = QColor("#121419")
        self._overlay_color = QColor("#121419")
        self._overlay_alpha = 153  # 0-255，默认 60% => 153

    # ---- 对外设置接口 ----
    def set_background(self, path: str | Path | None) -> None:
        """设置背景图片路径；传入 None 表示仅使用纯色背景。"""
        if path:
            pix = QPixmap(str(path))
            self._pixmap = pix if not pix.isNull() else None
        else:
            self._pixmap = None
        self.update()

    def set_mode(self, mode: str) -> None:
        """设置背景显示模式：cover / tile / stretch。"""
        if mode not in ("cover", "tile", "stretch"):
            mode = "cover"
        self._mode = mode
        self.update()

    def set_overlay_alpha(self, percent: int) -> None:
        """设置遮罩不透明度（0-100，百分比）。"""
        self._overlay_alpha = max(0, min(100, int(percent))) * 255 // 100
        self.update()

    def set_base_color(self, color: QColor) -> None:
        """设置背景底色（无图片或遮罩之下的颜色）。"""
        self._base_color = QColor(color)
        self.update()

    def set_overlay_color(self, color: QColor) -> None:
        """设置遮罩颜色（随主题切换明暗）。"""
        self._overlay_color = QColor(color)
        self.update()

    # ---- 绘制 ----
    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802  Qt 命名约定
        """绘制背景：底色 -> 图片 -> 半透明遮罩。"""
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.fillRect(self.rect(), self._base_color)
        if self._pixmap is not None:
            self._draw_pixmap(painter)
        overlay = QColor(
            self._overlay_color.red(),
            self._overlay_color.green(),
            self._overlay_color.blue(),
            self._overlay_alpha,
        )
        painter.fillRect(self.rect(), overlay)
        painter.end()

    def _draw_pixmap(self, painter: QPainter) -> None:
        """按当前模式绘制背景图。"""
        rect = self.rect()
        if self._mode == "stretch":
            painter.drawPixmap(rect, self._pixmap)
        elif self._mode == "tile":
            pw, ph = self._pixmap.width(), self._pixmap.height()
            for y in range(0, rect.height(), ph):
                for x in range(0, rect.width(), pw):
                    painter.drawPixmap(x, y, self._pixmap)
        else:  # cover：等比放大并居中裁剪
            scaled = self._pixmap.scaled(
                rect.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
            )
            x = (scaled.width() - rect.width()) // 2
            y = (scaled.height() - rect.height()) // 2
            painter.drawPixmap(rect, scaled, QRect(x, y, rect.width(), rect.height()))
