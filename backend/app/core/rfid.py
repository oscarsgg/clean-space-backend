def normalize_rfid_uid(uid: str) -> str:
    return "".join(character for character in uid if character not in " :-\t\r\n").upper()