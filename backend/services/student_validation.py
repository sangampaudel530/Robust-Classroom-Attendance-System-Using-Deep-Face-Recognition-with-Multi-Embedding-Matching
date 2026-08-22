"""Shared validation rules for student identifiers and image uploads."""

import re

ROLL_NO_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,32}$")
MAX_STUDENT_NAME_LENGTH = 128
MAX_PHOTO_BYTES = 10 * 1024 * 1024
MAX_PHOTOS_PER_REQUEST = 10


def normalize_student_info(roll_no: str, name: str) -> tuple[str, str]:
    roll_no = roll_no.strip()
    name = name.strip()
    if not roll_no or not name:
        raise ValueError("Roll number and name are required.")
    if not ROLL_NO_PATTERN.fullmatch(roll_no):
        raise ValueError(
            "Roll number may only contain letters, numbers, dots, underscores, "
            "and hyphens (maximum 32 characters)."
        )
    if len(name) > MAX_STUDENT_NAME_LENGTH:
        raise ValueError(f"Name must be {MAX_STUDENT_NAME_LENGTH} characters or fewer.")
    return roll_no, name


def is_valid_roll_no(roll_no: str) -> bool:
    return bool(ROLL_NO_PATTERN.fullmatch(roll_no))
