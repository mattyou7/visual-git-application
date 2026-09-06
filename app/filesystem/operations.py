from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


class FilesystemError(RuntimeError):
    """A user-facing filesystem operation failure."""


class FilesystemService:
    def list_directory(self, directory: Path) -> list[Path]:
        try:
            return sorted(directory.iterdir(), key=lambda path: (path.is_file(), path.name.casefold()))
        except OSError as error:
            raise FilesystemError(f"Could not read {directory.name or directory}: {error.strerror or error}") from error

    def create_folder(self, parent: Path, name: str) -> Path:
        target = self._child_path(parent, name)
        try:
            target.mkdir()
        except OSError as error:
            raise FilesystemError(f"Could not create folder '{name}': {error.strerror or error}") from error
        return target

    def rename(self, source: Path, new_name: str) -> Path:
        target = self._child_path(source.parent, new_name)
        try:
            source.rename(target)
        except OSError as error:
            raise FilesystemError(f"Could not rename '{source.name}': {error.strerror or error}") from error
        return target

    def delete(self, target: Path) -> None:
        try:
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            else:
                target.unlink()
        except OSError as error:
            raise FilesystemError(f"Could not delete '{target.name}': {error.strerror or error}") from error

    def copy_items(self, sources: list[Path], destination: Path) -> list[Path]:
        targets = self._validate_transfer(sources, destination)
        try:
            for source, target in zip(sources, targets):
                if source.is_dir() and not source.is_symlink():
                    shutil.copytree(source, target)
                else:
                    shutil.copy2(source, target)
        except OSError as error:
            raise FilesystemError(f"Could not copy files: {error.strerror or error}") from error
        return targets

    def move_items(self, sources: list[Path], destination: Path) -> list[Path]:
        targets = self._validate_transfer(sources, destination)
        try:
            for source, target in zip(sources, targets):
                shutil.move(str(source), str(target))
        except OSError as error:
            raise FilesystemError(f"Could not move files: {error.strerror or error}") from error
        return targets

    def open_path(self, target: Path) -> None:
        try:
            if sys.platform == "darwin":
                command = ["open", str(target)]
            elif sys.platform == "win32":
                getattr(os, "startfile")(str(target))
                return
            else:
                command = ["xdg-open", str(target)]
            subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as error:
            raise FilesystemError(f"Could not open '{target.name}': {error.strerror or error}") from error

    @staticmethod
    def _child_path(parent: Path, name: str) -> Path:
        clean_name = name.strip()
        if not clean_name or clean_name in {".", ".."} or Path(clean_name).name != clean_name:
            raise FilesystemError("Names must contain a single valid file or folder name.")
        target = parent / clean_name
        if target.exists():
            raise FilesystemError(f"'{clean_name}' already exists.")
        return target

    @staticmethod
    def _validate_transfer(sources: list[Path], destination: Path) -> list[Path]:
        if not sources:
            raise FilesystemError("Select at least one file or folder.")
        if not destination.is_dir():
            raise FilesystemError("Choose a folder as the paste destination.")
        resolved_destination = destination.resolve()
        targets: list[Path] = []
        for source in sources:
            if not source.exists():
                raise FilesystemError(f"'{source.name}' no longer exists.")
            if source.is_dir() and not source.is_symlink() and resolved_destination.is_relative_to(source.resolve()):
                raise FilesystemError(f"Cannot move or copy '{source.name}' into itself.")
            target = destination / source.name
            if target.exists() or target in targets:
                raise FilesystemError(f"'{target.name}' already exists in the destination.")
            targets.append(target)
        return targets
