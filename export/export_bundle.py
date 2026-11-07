"""Script di utilità per esportare l'intero progetto in:
1. Un unico file di testo strutturato (HYPE_BOT_BUNDLE.txt)
2. Un archivio compresso pulito (HYPE_BOT_CODE.zip)

Ideale per condividere l'intera codebase con agenti AI esterni (ChatGPT, Claude, Gemini)
superando il limite del numero massimo di allegati.
"""

import os
import zipfile

EXCLUDE_FILES = {
    "cronoMacd.txt",
    "paper_wallet.json",
    "fileCicloStart.txt",
    "fileCicloStart.txt.tmp",
    "paper_wallet.json.tmp",
    "HYPE_BOT_BUNDLE.txt",
    "HYPE_BOT_CODE.zip",
    "export_bundle.py",
}

EXCLUDE_DIRS = {
    "__pycache__",
    ".git",
    ".gemini",
    ".vscode",
    ".idea",
    "backtest_output",
    "output",
    "export",
}

INCLUDE_EXTS = {".py", ".md", ".txt", ".json", ".example"}


def get_project_files():
    export_dir = os.path.dirname(os.path.abspath(__file__))
    root = os.path.abspath(os.path.join(export_dir, ".."))
    file_list = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        for f in sorted(filenames):
            if f in EXCLUDE_FILES:
                continue
            if (f.startswith("cronoMacd") or f.startswith("paper_wallet")
                    or f.startswith("fileCicloStart") or f.startswith("bot_state")
                    or f.endswith(".tmp")):
                continue
            ext = os.path.splitext(f)[1].lower()
            if ext in INCLUDE_EXTS or f in ("Dockerfile", ".env.example", ".gitignore"):
                rel_path = os.path.relpath(os.path.join(dirpath, f), root)
                file_list.append((rel_path, os.path.join(dirpath, f)))
    return root, export_dir, sorted(file_list)


def create_bundle(out_dir, file_list, out_file="HYPE_BOT_BUNDLE.txt"):
    target_path = os.path.join(out_dir, out_file)
    with open(target_path, "w", encoding="utf-8") as out:
        out.write("=" * 80 + "\n")
        out.write("HYPE TRAIL BOT — CODEBASE COMPLETA DEL PROGETTO\n")
        out.write("File generato per analisi da parte di agenti AI esterni\n")
        out.write("=" * 80 + "\n\n")

        out.write("INDICE DEI FILE INCLUSI:\n")
        for rel_path, _ in file_list:
            out.write(f"- {rel_path}\n")
        out.write("\n" + "=" * 80 + "\n\n")

        for rel_path, full_path in file_list:
            out.write("\n" + "=" * 80 + "\n")
            out.write(f"FILE: {rel_path}\n")
            out.write("=" * 80 + "\n\n")
            try:
                with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                    out.write(f.read())
            except Exception as e:
                out.write(f"[Errore lettura file: {e}]\n")
            out.write("\n")

    print(f"Creato file bundle unico: {target_path}")


def create_zip(out_dir, file_list, out_zip="HYPE_BOT_CODE.zip"):
    zip_path = os.path.join(out_dir, out_zip)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel_path, full_path in file_list:
            zf.write(full_path, rel_path)
    print(f"Creato archivio ZIP: {zip_path}")


def main():
    root, export_dir, files = get_project_files()
    print(f"Trovati {len(files)} file di codice/documentazione.")
    create_bundle(export_dir, files)
    create_zip(export_dir, files)
    print("\nCompletato! File esportati in 'export/'.")


if __name__ == "__main__":
    main()

