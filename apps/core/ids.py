"""UUID generator. Mirrors the Go service which writes RFC4122 v4 strings (with
dashes, lowercase) into VARCHAR(36)/CHAR(36) primary keys."""

import uuid


def new_id() -> str:
    return str(uuid.uuid4())
