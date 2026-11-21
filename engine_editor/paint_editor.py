"""A small Tkinter drawing window that exports artwork as a PNG sprite."""

import base64
import io
import tkinter as tk
from tkinter import colorchooser, messagebox

from PIL import Image, ImageDraw


class PaintEditor:
    """Let the user draw, edit, and return one image to the block editor."""

    def __init__(self, parent, on_save):
        """Create the drawing tools and remember how to return the finished PNG."""
        self.window = tk.Toplevel(parent)
        self.window.title("Draw a sprite")
        self.window.geometry("850x620")
        self.window.transient(parent)
        self.on_save = on_save
        self.color = "#2878d0"
        self.tool = "pencil"
        self.filled = False
        # Store drawing instructions so shapes can be edited and exported later.
        self.shapes = []
        self.current_shape = None
        self.start_point = None
        self.photo_canvas_items = {}

        toolbar = tk.Frame(self.window, bg="#f1f3f5")
        toolbar.pack(fill="x", padx=10, pady=10)
        for label, tool in (
            ("Pencil", "pencil"),
            ("Line", "line"),
            ("Rectangle", "rectangle"),
            ("Oval", "oval"),
            ("Fill", "fill"),
            ("Sample", "sample"),
        ):
            tk.Button(
                toolbar,
                text=label,
                command=lambda selected=tool: self.select_tool(selected),
                relief="flat",
                padx=9,
            ).pack(side="left", padx=2)

        self.color_button = tk.Button(
            toolbar, text="Paint color", command=self.choose_color,
            bg=self.color, fg="white", relief="flat", padx=10,
        )
        self.color_button.pack(side="left", padx=(10, 4))
        self.fill_button = tk.Button(
            toolbar, text="Outline only", command=self.toggle_fill,
            relief="flat", padx=9,
        )
        self.fill_button.pack(side="left", padx=4)
        tk.Button(
            toolbar, text="Clear", command=self.clear,
            relief="flat", padx=9,
        ).pack(side="left", padx=4)
        tk.Button(
            toolbar, text="Use drawing", command=self.save,
            bg="#39a96b", fg="white", relief="flat", padx=12,
        ).pack(side="right", padx=4)

        self.canvas = tk.Canvas(
            self.window, bg="white", highlightthickness=1,
            highlightbackground="#cbd2d9", cursor="crosshair",
        )
        self.canvas.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.canvas.bind("<ButtonPress-1>", self.begin_action)
        self.canvas.bind("<B1-Motion>", self.continue_action)
        self.canvas.bind("<ButtonRelease-1>", self.finish_action)
        self.window.bind("<Escape>", lambda _event: self.window.destroy())

    def select_tool(self, tool):
        """Choose the active drawing, fill, or color-sampling tool."""
        self.tool = tool
        self.canvas.configure(cursor="crosshair" if tool != "fill" else "hand2")

    def choose_color(self):
        """Ask Windows for a color and update the toolbar preview."""
        selected = colorchooser.askcolor(self.color, parent=self.window)[1]
        if selected:
            self.color = selected
            self.color_button.configure(bg=selected)

    def toggle_fill(self):
        """Switch rectangle and oval tools between outline and filled modes."""
        self.filled = not self.filled
        self.fill_button.configure(text="Filled" if self.filled else "Outline only")

    def begin_action(self, event):
        """Start a stroke or shape, or apply fill/sample at the click location."""
        point = (event.x, event.y)
        self.start_point = point
        if self.tool == "sample":
            self.sample_at(point)
        elif self.tool == "fill":
            self.fill_at(point)
        elif self.tool == "pencil":
            item = self.canvas.create_line(
                event.x, event.y, event.x + 1, event.y + 1,
                fill=self.color, width=4, capstyle=tk.ROUND, smooth=True,
                tags=("paint-shape",),
            )
            shape = {
                "kind": "pencil",
                "points": [point],
                "color": self.color,
                "fill": "",
                "filled": False,
                "item": item,
            }
            self.shapes.append(shape)
            self.current_shape = shape
        else:
            shape = {
                "kind": self.tool,
                "points": [point, point],
                "color": self.color,
                "fill": self.color if self.filled else "",
                "filled": self.filled,
            }
            self.current_shape = shape
            self.shapes.append(shape)
            shape["item"] = self.draw_shape(shape, preview=True)

    def continue_action(self, event):
        """Update the live pencil stroke or resize the shape preview."""
        if self.current_shape is None or self.start_point is None:
            return
        shape = self.current_shape
        point = (event.x, event.y)
        if shape["kind"] == "pencil":
            shape["points"].append(point)
            self.canvas.coords(shape["item"], *self.flatten(shape["points"]))
        else:
            shape["points"][1] = point
            self.canvas.delete(shape["item"])
            shape["item"] = self.draw_shape(shape, preview=True)

    def finish_action(self, event):
        """Save the final pointer location and finish the current gesture."""
        if self.current_shape is not None and self.current_shape["kind"] != "pencil":
            self.current_shape["points"][1] = (event.x, event.y)
        self.current_shape = None
        self.start_point = None

    def flatten(self, points):
        """Convert (x, y) pairs into the coordinate list Tkinter expects."""
        return [coordinate for point in points for coordinate in point]

    def draw_shape(self, shape, preview=False):
        """Draw one stored line, rectangle, or oval onto the canvas."""
        x1, y1 = shape["points"][0]
        x2, y2 = shape["points"][-1]
        item_options = {
            "outline": shape["color"],
            "width": 3,
            "tags": ("paint-shape",),
        }
        if shape["kind"] == "line":
            return self.canvas.create_line(
                x1, y1, x2, y2, fill=shape["color"], width=3,
                tags=("paint-shape",),
            )
        item_options["fill"] = shape["fill"]
        if shape["kind"] == "rectangle":
            return self.canvas.create_rectangle(x1, y1, x2, y2, **item_options)
        return self.canvas.create_oval(x1, y1, x2, y2, **item_options)

    def shape_at(self, point):
        """Find the topmost stored drawing item nearest the clicked point."""
        item = self.canvas.find_closest(*point)
        if not item or "paint-shape" not in self.canvas.gettags(item[0]):
            return None
        for shape in reversed(self.shapes):
            if shape.get("item") == item[0]:
                return shape
        return None

    def fill_at(self, point):
        """Apply the selected color to a shape's fill or stroke."""
        shape = self.shape_at(point)
        if shape is None:
            return
        if shape["kind"] == "line" or shape["kind"] == "pencil":
            shape["color"] = self.color
        else:
            shape["filled"] = True
            shape["fill"] = self.color
        self.redraw()

    def sample_at(self, point):
        """Pick a previously drawn shape's color as the active paint color."""
        shape = self.shape_at(point)
        if shape is None:
            return
        self.color = shape["fill"] or shape["color"]
        self.color_button.configure(bg=self.color)

    def clear(self):
        """Remove every shape from both the drawing model and canvas."""
        self.shapes.clear()
        self.canvas.delete("all")

    def redraw(self):
        """Recreate the canvas from the saved shape instructions."""
        self.canvas.delete("all")
        for shape in self.shapes:
            if shape["kind"] == "pencil":
                shape["item"] = self.canvas.create_line(
                    *self.flatten(shape["points"]),
                    fill=shape["color"], width=4, capstyle=tk.ROUND,
                    smooth=True, tags=("paint-shape",),
                )
            else:
                shape["item"] = self.draw_shape(shape)

    def save(self):
        """Render all shapes to PNG, send it to the editor, then close."""
        self.window.update_idletasks()
        width = max(self.canvas.winfo_width(), 1)
        height = max(self.canvas.winfo_height(), 1)
        image = Image.new("RGBA", (width, height), "white")
        draw = ImageDraw.Draw(image)
        for shape in self.shapes:
            x1, y1 = shape["points"][0]
            x2, y2 = shape["points"][-1]
            if shape["kind"] == "pencil":
                draw.line(
                    shape["points"], fill=shape["color"], width=4,
                    joint="curve",
                )
            elif shape["kind"] == "line":
                draw.line((x1, y1, x2, y2), fill=shape["color"], width=3)
            elif shape["kind"] == "rectangle":
                draw.rectangle(
                    (x1, y1, x2, y2),
                    fill=shape["fill"] or None,
                    outline=shape["color"],
                    width=3,
                )
            elif shape["kind"] == "oval":
                draw.ellipse(
                    (x1, y1, x2, y2),
                    fill=shape["fill"] or None,
                    outline=shape["color"],
                    width=3,
                )

        # Encode the PNG as text-safe Base64 so it can be stored in a project.
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        encoded_image = base64.b64encode(buffer.getvalue()).decode("ascii")
        if not self.on_save(encoded_image):
            return
        self.window.destroy()
