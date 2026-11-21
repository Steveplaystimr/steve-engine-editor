"""Costume hitbox editor for articulated Box2D sprite parts."""

import copy
import math
import tkinter as tk
from tkinter import messagebox, ttk

from PIL import ImageTk


class PhysicsHitboxEditor:
    """Draw rectangular physics parts and revolute-joint pivots on a costume."""

    def __init__(self, parent, image, parts, on_save):
        self.window = tk.Toplevel(parent)
        self.window.title("Physics hitboxes")
        self.window.geometry("900x620")
        self.window.transient(parent)
        self.image = image.convert("RGBA")
        self.parts = copy.deepcopy(parts)
        self.on_save = on_save
        self.scale = min(1.0, 620 / self.image.width, 500 / self.image.height)
        self.display_size = (
            max(1, round(self.image.width * self.scale)),
            max(1, round(self.image.height * self.scale)),
        )
        self.origin = (18, 18)
        self.start_point = None
        self.preview_id = None
        self.pivot_mode = False
        self.selected_index = None

        content = tk.Frame(self.window)
        content.pack(fill="both", expand=True, padx=10, pady=10)
        self.canvas = tk.Canvas(
            content, width=self.display_size[0] + 36,
            height=self.display_size[1] + 36,
            bg="#252a35", highlightthickness=0, cursor="crosshair",
        )
        self.canvas.pack(side="left", fill="both", expand=True)
        rendered = self.image.resize(self.display_size)
        self.photo = ImageTk.PhotoImage(rendered)
        self.canvas.create_image(*self.origin, image=self.photo, anchor="nw")
        self.controls = tk.Frame(content, width=230)
        self.controls.pack(side="right", fill="y", padx=(12, 0))
        tk.Label(
            self.controls,
            text="Drag a rectangle over an image part. Set a parent to hinge "
            "that part at a pivot.",
            wraplength=220, justify="left",
        ).pack(anchor="w", pady=(0, 8))
        self.part_list = tk.Listbox(self.controls, height=8, exportselection=False)
        self.part_list.pack(fill="x")
        self.part_list.bind("<<ListboxSelect>>", self.select_part)
        tk.Label(self.controls, text="Part name").pack(anchor="w", pady=(8, 0))
        self.name_var = tk.StringVar()
        ttk.Entry(self.controls, textvariable=self.name_var).pack(fill="x")
        tk.Label(self.controls, text="Body type").pack(anchor="w", pady=(6, 0))
        self.type_var = tk.StringVar(value="dynamic")
        ttk.Combobox(
            self.controls, textvariable=self.type_var,
            values=("static", "kinematic", "dynamic"), state="readonly",
        ).pack(fill="x")
        self.friction_var = tk.StringVar(value="0.3")
        self.restitution_var = tk.StringVar(value="0")
        for label, variable in (
            ("Friction (0-1)", self.friction_var),
            ("Bounce / restitution (0-1)", self.restitution_var),
        ):
            tk.Label(self.controls, text=label).pack(anchor="w", pady=(6, 0))
            ttk.Entry(self.controls, textvariable=variable).pack(fill="x")
        tk.Label(self.controls, text="Hinge parent (optional)").pack(
            anchor="w", pady=(6, 0),
        )
        self.parent_var = tk.StringVar()
        self.parent_menu = ttk.Combobox(
            self.controls, textvariable=self.parent_var, state="readonly",
        )
        self.parent_menu.pack(fill="x")
        tk.Button(
            self.controls, text="Apply part settings", command=self.apply_part,
        ).pack(fill="x", pady=(8, 4))
        tk.Button(
            self.controls, text="Set hinge pivot",
            command=self.begin_pivot_selection,
        ).pack(fill="x", pady=4)
        tk.Button(
            self.controls, text="Delete selected part",
            command=self.delete_part,
        ).pack(fill="x", pady=4)
        tk.Button(
            self.controls, text="Save hitboxes", command=self.save,
            bg="#39a96b", fg="white",
        ).pack(fill="x", pady=(14, 4))
        tk.Button(
            self.controls, text="Cancel", command=self.window.destroy,
        ).pack(fill="x", pady=4)
        self.canvas.bind("<ButtonPress-1>", self.begin_drag)
        self.canvas.bind("<B1-Motion>", self.drag)
        self.canvas.bind("<ButtonRelease-1>", self.end_drag)
        self.refresh_parts()

    def image_point(self, event):
        x = (event.x - self.origin[0]) / self.scale
        y = (event.y - self.origin[1]) / self.scale
        return (
            min(max(x, 0.0), float(self.image.width)),
            min(max(y, 0.0), float(self.image.height)),
        )

    def canvas_point(self, x, y):
        return (
            self.origin[0] + x * self.scale,
            self.origin[1] + y * self.scale,
        )

    def begin_drag(self, event):
        point = self.image_point(event)
        if self.pivot_mode:
            if self.selected_index is not None:
                self.parts[self.selected_index]["pivot_x"] = point[0]
                self.parts[self.selected_index]["pivot_y"] = point[1]
                self.pivot_mode = False
                self.refresh_parts(select=self.selected_index)
            return
        if not self._store_selected():
            return
        self.start_point = point
        x, y = self.canvas_point(*point)
        self.preview_id = self.canvas.create_rectangle(
            x, y, x, y, outline="#ffcc4d", width=2,
        )

    def drag(self, event):
        if self.start_point is None or self.preview_id is None:
            return
        x1, y1 = self.canvas_point(*self.start_point)
        x2, y2 = self.canvas_point(*self.image_point(event))
        self.canvas.coords(self.preview_id, x1, y1, x2, y2)

    def end_drag(self, event):
        if self.start_point is None or self.pivot_mode:
            return
        end_point = self.image_point(event)
        x = min(self.start_point[0], end_point[0])
        y = min(self.start_point[1], end_point[1])
        width = abs(end_point[0] - self.start_point[0])
        height = abs(end_point[1] - self.start_point[1])
        if width >= 2 and height >= 2:
            names = {part.get("name") for part in self.parts}
            index = 1
            while f"Part {index}" in names:
                index += 1
            self.parts.append({
                "name": f"Part {index}",
                "x": x,
                "y": y,
                "width": width,
                "height": height,
                "body_type": "dynamic",
                "friction": 0.3,
                "restitution": 0.0,
                "parent": "",
                "pivot_x": x + width / 2,
                "pivot_y": y + height / 2,
            })
            self.refresh_parts(select=len(self.parts) - 1)
        self.start_point = None
        if self.preview_id is not None:
            self.canvas.delete(self.preview_id)
            self.preview_id = None

    def refresh_parts(self, select=None):
        self.part_list.delete(0, tk.END)
        for index, part in enumerate(self.parts):
            self.part_list.insert(tk.END, part.get("name", f"Part {index + 1}"))
        self.parent_menu.configure(
            values=("", *(
                part["name"] for index, part in enumerate(self.parts)
                if index != select
            )),
        )
        if select is not None:
            self.selected_index = select
            self.part_list.selection_set(select)
            self._load_selected()
        self.redraw_regions()

    def select_part(self, _event=None):
        selected = self.part_list.curselection()
        if selected:
            if not self._store_selected():
                return
            self.selected_index = selected[0]
            self._load_selected()

    def _load_selected(self):
        if self.selected_index is None:
            return
        part = self.parts[self.selected_index]
        self.name_var.set(part["name"])
        self.type_var.set(part.get("body_type", "dynamic"))
        self.friction_var.set(str(part.get("friction", 0.3)))
        self.restitution_var.set(str(part.get("restitution", 0.0)))
        self.parent_var.set(part.get("parent", ""))
        self.parent_menu.configure(values=("", *(
            item["name"] for index, item in enumerate(self.parts)
            if index != self.selected_index
        )))

    def redraw_regions(self):
        self.canvas.delete("physics-part")
        for index, part in enumerate(self.parts):
            x1, y1 = self.canvas_point(part["x"], part["y"])
            x2, y2 = self.canvas_point(
                part["x"] + part["width"], part["y"] + part["height"],
            )
            color = "#ffcc4d" if index == self.selected_index else "#42d392"
            self.canvas.create_rectangle(
                x1, y1, x2, y2, outline=color, width=2,
                tags=("physics-part",),
            )
            px, py = self.canvas_point(part["pivot_x"], part["pivot_y"])
            self.canvas.create_line(
                px - 5, py, px + 5, py, fill="#ff6680", width=2,
                tags=("physics-part",),
            )
            self.canvas.create_line(
                px, py - 5, px, py + 5, fill="#ff6680", width=2,
                tags=("physics-part",),
            )

    def apply_part(self):
        if self._store_selected():
            self.refresh_parts(select=self.selected_index)

    def _store_selected(self):
        if self.selected_index is None:
            return True
        name = self.name_var.get().strip()
        if not name or any(
            part["name"] == name for index, part in enumerate(self.parts)
            if index != self.selected_index
        ):
            messagebox.showerror(
                "Invalid part name", "Part names must be unique and non-empty.",
                parent=self.window,
            )
            return False
        parent = self.parent_var.get().strip()
        if parent == name or (
            parent and parent not in {
                part["name"] for index, part in enumerate(self.parts)
                if index != self.selected_index
            }
        ):
            messagebox.showerror(
                "Invalid hinge parent", "A physics part cannot hinge to itself.",
                parent=self.window,
            )
            return False
        try:
            friction = float(self.friction_var.get())
            restitution = float(self.restitution_var.get())
        except ValueError:
            messagebox.showerror(
                "Invalid physics material",
                "Friction and bounce must be numbers from 0 to 1.",
                parent=self.window,
            )
            return False
        if (
            not math.isfinite(friction) or not math.isfinite(restitution)
            or not 0 <= friction <= 1 or not 0 <= restitution <= 1
        ):
            messagebox.showerror(
                "Invalid physics material",
                "Friction and bounce must be numbers from 0 to 1.",
                parent=self.window,
            )
            return False
        part = self.parts[self.selected_index]
        old_name = part["name"]
        part["name"] = name
        if old_name != name:
            for child in self.parts:
                if child.get("parent") == old_name:
                    child["parent"] = name
        part["body_type"] = self.type_var.get()
        part["friction"] = friction
        part["restitution"] = restitution
        part["parent"] = parent
        return True

    def begin_pivot_selection(self):
        if self.selected_index is None:
            return
        if not self._store_selected():
            return
        if not self.parent_var.get().strip():
            messagebox.showinfo(
                "Choose a hinge parent",
                "Select a parent part before choosing this part's hinge pivot.",
                parent=self.window,
            )
            return
        self.pivot_mode = True
        self.window.title("Physics hitboxes - click to set hinge pivot")

    def delete_part(self):
        if self.selected_index is None:
            return
        removed = self.parts.pop(self.selected_index)
        for part in self.parts:
            if part.get("parent") == removed["name"]:
                part["parent"] = ""
        self.selected_index = None
        self.refresh_parts()

    def save(self):
        if not self._store_selected():
            return
        self.on_save(self.parts)
        self.window.destroy()
