
import tkinter as tk


class StackViewer:
    def __init__(self, stack, interpreter):
        self.stack = stack
        self.interpreter = interpreter
        self.running = False

        self.root = tk.Tk()
        self.root.title("Stacky")

        self.listbox = tk.Listbox(
            self.root,
            font=("Consolas", 12),
            width=30,
            height=20
        )
        self.scrollbar = tk.Scrollbar(
            self.root,
            orient=tk.VERTICAL,
            command=self.listbox.yview
        )
        self.listbox.config(yscrollcommand=self.scrollbar.set)

        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.stepButton = tk.Button(
            self.root,
            text="Step",
            font=("Consolas", 12, 'italic'),
            width=10,
            command=self.step,
            bg='yellow'
        )
        self.runButton = tk.Button(
            self.root,
            text="Run",
            font=("Consolas", 12, "bold"),
            width=10,
            command=self.run,
            bg='lime',
        )
        self.rerunButton = tk.Button(
            self.root,
            text="Rerun",
            font=("Consolas", 12, 'bold'),
            width=10,
            command=self.rerun,
            bg='red'
        )
        self.resetButton = tk.Button(
            self.root,
            text="Reset",
            font=("Consolas", 12, 'bold'),
            width=10,
            command=lambda: interpreter.rerun(False),
            bg='red'
        )
        self.runButton.pack()
        self.stepButton.pack()
        self.resetButton.pack()
        self.rerunButton.pack()
        
        self.update()

    def update(self):
        yview = self.listbox.yview()

        self.listbox.delete(0, tk.END)

        for i, value in enumerate(reversed(self.stack)):
            self.listbox.insert(tk.END, f"{i}: {value!r}")

        self.listbox.yview_moveto(yview[0])

        self.root.after(50, self.update)
    def step(self):
        self.pause()
        self.interpreter.step()
    def run(self):
        if self.running:
            return

        self.running = True
        self.run_step()
    def run_step(self):
        if not self.running:
            return

        if self.interpreter.step():
            self.root.after(50, self.run_step)
        else:
            self.pause()
    def pause(self):
        self.running = False
    def rerun(self):
        self.interpreter.rerun()

    def mainloop(self):
        self.root.mainloop()
