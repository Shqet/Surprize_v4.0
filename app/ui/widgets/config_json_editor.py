from __future__ import annotations

from typing import Any, Optional, Set
import copy
import json

from PyQt6.QtCore import Qt, QTimer, QModelIndex
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QFileDialog,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
    QStyledItemDelegate,
)

_KEY_LABELS_RU: dict[str, str] = {
    "simulation": "Симуляция",
    "projectile": "Снаряд",
    "rotation": "Вращение",
    "initial_conditions": "Начальные условия",
    "dt": "Шаг интегрирования dt",
    "t_max": "Макс. время t_max",
    "max_steps": "Макс. число шагов",
    "m": "Масса m",
    "S": "Площадь S",
    "C_L": "Коэф. подъемной силы C_L",
    "C_mp": "Коэф. демпфирования C_mp",
    "g": "Ускорение g",
    "Ix": "Момент инерции Ix",
    "Iy": "Момент инерции Iy",
    "Iz": "Момент инерции Iz",
    "k_stab": "Коэф. стабилизации",
    "V0": "Начальная скорость V0",
    "theta_deg": "Угол тангажа theta (град)",
    "psi_deg": "Угол рыскания psi (град)",
    "X0": "Начальная координата X0",
    "Y0": "Начальная координата Y0",
    "Z0": "Начальная координата Z0",
    "omega_body": "Начальная угловая скорость [wx,wy,wz]",
}


class _ValueOnlyEditDelegate(QStyledItemDelegate):
    """
    Делегат запрещает редактирование колонки 0 ("Параметр"),
    разрешает только колонку 1 ("Значение").
    """

    def createEditor(self, parent, option, index: QModelIndex):
        if index.column() != 1:
            return None
        return super().createEditor(parent, option, index)


class ConfigJsonEditor(QWidget):
    def __init__(self, initial_config: dict[str, Any], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        self._initial_config: dict[str, Any] = copy.deepcopy(initial_config)
        self._current_config: dict[str, Any] = copy.deepcopy(initial_config)

        self._tree_updating: bool = False
        self._expanded_paths: Set[str] = set()

        self.setObjectName("w_cfg_editor_container")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self._build_ui()
        self._populate_config_tree()

    # ---------- public API ----------

    def get_config(self) -> dict[str, Any]:
        return self._current_config

    def set_config(self, cfg: dict[str, Any]) -> None:
        self._current_config = copy.deepcopy(cfg)
        self._populate_config_tree()
        self.set_status("Конфигурация загружена в память")

    def set_initial_config(self, cfg: dict[str, Any]) -> None:
        self._initial_config = copy.deepcopy(cfg)

    def reset_to_initial(self) -> None:
        self._current_config = copy.deepcopy(self._initial_config)
        self._populate_config_tree()
        self.set_status("Конфигурация сброшена к исходной (в памяти)")

    # ---------- UI ----------

    def _build_ui(self) -> None:
        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(6)

        self.tree = QTreeWidget()
        self.tree.setObjectName("tw_config_json")
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["Параметр", "Значение"])
        self.tree.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        # Жёстко запрещаем редактирование колонки 0
        self.tree.setItemDelegate(_ValueOnlyEditDelegate(self.tree))

        self.tree.itemChanged.connect(self._on_tree_item_changed)
        self.tree.itemExpanded.connect(self._on_item_expanded)
        self.tree.itemCollapsed.connect(self._on_item_collapsed)

        vbox.addWidget(self.tree, stretch=1)

        self.status = QLabel("Готово")
        self.status.setObjectName("lbl_config_status")
        self.status.setWordWrap(True)
        self.status.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        vbox.addWidget(self.status, stretch=0)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        self.btn_load = QPushButton("Загрузить JSON…")
        self.btn_load.setObjectName("btn_load_config_json")
        self.btn_load.clicked.connect(self.on_load_json_clicked)
        row.addWidget(self.btn_load)

        self.btn_save = QPushButton("Сохранить JSON…")
        self.btn_save.setObjectName("btn_save_config_json")
        self.btn_save.clicked.connect(self.on_save_json_clicked)
        row.addWidget(self.btn_save)

        self.btn_reset = QPushButton("Сбросить")
        self.btn_reset.setObjectName("btn_reset_config")
        self.btn_reset.clicked.connect(self.reset_to_initial)
        row.addWidget(self.btn_reset)

        row.addStretch(1)
        vbox.addLayout(row)

    def set_status(self, text: str) -> None:
        self.status.setText(text)

    # ---------- Load / Save ----------

    def on_load_json_clicked(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Загрузить конфигурацию JSON",
            "",
            "JSON (*.json);;Все файлы (*.*)",
        )
        if not path:
            return

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            self.set_status(f"Ошибка загрузки JSON: {e!r}")
            return

        if not isinstance(data, dict):
            self.set_status("Ошибка: корневой JSON должен быть объектом (dict)")
            return

        self._current_config = data
        self._populate_config_tree()
        self.set_status(f"JSON загружен: {path}")

    def on_save_json_clicked(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Сохранить конфигурацию JSON",
            "config.json",
            "JSON (*.json);;Все файлы (*.*)",
        )
        if not path:
            return

        if "." not in path.split("/")[-1] and "." not in path.split("\\")[-1]:
            path = path + ".json"

        try:
            text = json.dumps(self._current_config, ensure_ascii=False, indent=2, sort_keys=True)
        except Exception as e:
            self.set_status(f"Ошибка сериализации JSON: {e!r}")
            return

        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        except Exception as e:
            self.set_status(f"Ошибка сохранения JSON: {e!r}")
            return

        self.set_status(f"JSON сохранён: {path}")

    # ---------- expanded-state tracking ----------

    def _on_item_expanded(self, item: QTreeWidgetItem) -> None:
        if self._tree_updating:
            return
        p = self._path_for_item(item)
        if p:
            self._expanded_paths.add(p)

    def _on_item_collapsed(self, item: QTreeWidgetItem) -> None:
        if self._tree_updating:
            return
        p = self._path_for_item(item)
        if p:
            to_remove = {x for x in self._expanded_paths if x == p or x.startswith(p + ".")}
            self._expanded_paths.difference_update(to_remove)

    def _path_for_item(self, item: QTreeWidgetItem) -> str:
        parts: list[str] = []
        cur = item
        while cur is not None:
            parent = cur.parent()
            if parent is None:
                break  # root "config_json"
            key = cur.data(0, Qt.ItemDataRole.UserRole)
            parts.append(str(key) if isinstance(key, str) and key else cur.text(0))
            cur = parent
        parts.reverse()
        return ".".join(parts)

    def _normalize_expanded(self, paths: Set[str]) -> Set[str]:
        out: Set[str] = set()
        for p in paths:
            if not p:
                continue
            chunks = p.split(".")
            for i in range(1, len(chunks) + 1):
                out.add(".".join(chunks[:i]))
        return out

    def _restore_expanded_paths_now(self, paths: Set[str]) -> None:
        top = self.tree.topLevelItem(0)
        if top is None:
            return

        self._tree_updating = True
        self.tree.blockSignals(True)
        try:
            top.setExpanded(True)
            if not paths:
                return

            paths = self._normalize_expanded(paths)
            for i in range(top.childCount()):
                self._apply_expanded_recursive(top.child(i), prefix="", expanded_paths=paths)
        finally:
            self.tree.blockSignals(False)
            self._tree_updating = False

    def _apply_expanded_recursive(self, item: QTreeWidgetItem, prefix: str, expanded_paths: Set[str]) -> None:
        key = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(key, str) or not key:
            key = item.text(0)
        path = f"{prefix}.{key}" if prefix else key

        if item.childCount() > 0:
            item.setExpanded(path in expanded_paths)
            for i in range(item.childCount()):
                self._apply_expanded_recursive(item.child(i), path, expanded_paths)

    # ---------- tree build ----------

    def _populate_config_tree(self) -> None:
        restore_paths = set(self._expanded_paths)

        self._tree_updating = True
        self.tree.blockSignals(True)
        try:
            self.tree.clear()

            root = QTreeWidgetItem(["config_json", ""])
            root.setFlags(root.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.tree.addTopLevelItem(root)
            root.setExpanded(True)

            self._add_tree_nodes(parent=root, value=self._current_config, path=[])
            self.tree.resizeColumnToContents(0)
        finally:
            self.tree.blockSignals(False)
            self._tree_updating = False

        QTimer.singleShot(0, lambda: self._restore_expanded_paths_now(restore_paths))

    def _add_tree_nodes(self, parent: QTreeWidgetItem, value: Any, path: list[str]) -> None:
        if isinstance(value, dict):
            for k in sorted(value.keys()):
                v = value[k]
                child_path = path + [str(k)]

                item = QTreeWidgetItem([self._display_name_for_key(str(k)), self._value_label(v)])
                item.setData(0, Qt.ItemDataRole.UserRole, str(k))

                if isinstance(v, dict):
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                else:
                    # leaf nodes: editable (delegate ограничит только колонкой 1)
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
                    item.setData(1, Qt.ItemDataRole.UserRole, child_path)  # path
                    item.setData(1, Qt.ItemDataRole.UserRole + 1, self._kind_code(v))  # kind
                    item.setData(1, Qt.ItemDataRole.UserRole + 2, item.text(1))  # old text

                parent.addChild(item)

                if isinstance(v, dict):
                    self._add_tree_nodes(item, v, child_path)
        else:
            item = QTreeWidgetItem(["<value>", self._value_label(value)])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
            item.setData(1, Qt.ItemDataRole.UserRole, path)
            item.setData(1, Qt.ItemDataRole.UserRole + 1, self._kind_code(value))
            item.setData(1, Qt.ItemDataRole.UserRole + 2, item.text(1))
            parent.addChild(item)

    def _display_name_for_key(self, key: str) -> str:
        ru = _KEY_LABELS_RU.get(key)
        if not ru:
            return key
        return f"{ru} ({key})"

    # ---------- editing / validation ----------

    def _on_tree_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._tree_updating:
            return
        if column != 1:
            return

        path = item.data(1, Qt.ItemDataRole.UserRole)
        kind = item.data(1, Qt.ItemDataRole.UserRole + 1)
        old_text = item.data(1, Qt.ItemDataRole.UserRole + 2)

        if not isinstance(path, list) or not isinstance(kind, str):
            return

        new_text = item.text(1)
        ok, parsed_or_err = self._parse_typed_value(kind, new_text)

        if not ok:
            self._tree_updating = True
            try:
                item.setText(1, str(old_text) if old_text is not None else "")
            finally:
                self._tree_updating = False
            self.set_status(f"Ошибка ввода: {parsed_or_err}")
            return

        parsed = parsed_or_err

        try:
            self._set_in_config(self._current_config, path, parsed)
        except Exception as e:
            self._tree_updating = True
            try:
                item.setText(1, str(old_text) if old_text is not None else "")
            finally:
                self._tree_updating = False
            self.set_status(f"Не удалось применить значение: {e!r}")
            return

        self._tree_updating = True
        try:
            item.setData(1, Qt.ItemDataRole.UserRole + 2, item.text(1))
        finally:
            self._tree_updating = False

        self.set_status(f"Изменено: {'.'.join(path)}")

    def _parse_typed_value(self, kind: str, text: str) -> tuple[bool, Any]:
        s = text.strip()

        if kind == "bool":
            lowered = s.lower()
            if lowered in ("true", "1", "да", "yes", "y"):
                return True, True
            if lowered in ("false", "0", "нет", "no", "n"):
                return True, False
            return False, "ожидается bool: true/false, 1/0, да/нет"

        if kind == "int":
            try:
                return True, int(s)
            except Exception:
                return False, "ожидается целое число (int)"

        if kind == "float":
            try:
                ss = s.replace(",", ".")
                return True, float(ss)
            except Exception:
                return False, "ожидается число (float)"

        if kind == "str":
            return True, text

        if kind == "list_num":
            try:
                arr = json.loads(s)
            except Exception:
                return False, "ожидается JSON-массив, например: [0, 0, 100]"
            if not isinstance(arr, list):
                return False, "ожидается JSON-массив (list)"
            if not all(isinstance(x, (int, float)) for x in arr):
                return False, "в массиве допускаются только числа"
            return True, arr

        if kind == "list":
            try:
                arr = json.loads(s)
            except Exception:
                return False, "ожидается JSON-массив, например: [1, 2, 3]"
            if not isinstance(arr, list):
                return False, "ожидается JSON-массив (list)"
            return True, arr

        return False, f"тип '{kind}' пока не поддержан для редактирования"

    def _set_in_config(self, cfg: dict[str, Any], path: list[str], value: Any) -> None:
        if not path:
            raise ValueError("empty path")
        node: Any = cfg
        for key in path[:-1]:
            if not isinstance(node, dict):
                raise TypeError("path points into non-dict")
            if key not in node:
                raise KeyError(key)
            node = node[key]
        last = path[-1]
        if not isinstance(node, dict):
            raise TypeError("parent is not dict")
        node[last] = value

    # ---------- formatting ----------

    def _kind_code(self, v: Any) -> str:
        if isinstance(v, bool):
            return "bool"
        if isinstance(v, int):
            return "int"
        if isinstance(v, float):
            return "float"
        if isinstance(v, str):
            return "str"
        if isinstance(v, list):
            if all(isinstance(x, (int, float)) for x in v):
                return "list_num"
            return "list"
        if isinstance(v, dict):
            return "dict"
        if v is None:
            return "null"
        return "other"

    def _value_label(self, v: Any) -> str:
        if isinstance(v, dict):
            return ""
        if isinstance(v, list):
            try:
                return json.dumps(v, ensure_ascii=False)
            except Exception:
                return str(v)
        if isinstance(v, bool):
            return "true" if v else "false"
        return str(v)
