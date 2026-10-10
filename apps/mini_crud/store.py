from dataclasses import dataclass

_EDITABLE_FIELDS = frozenset({"name", "bench", "note"})


@dataclass(frozen=True)
class Record:
    id: str
    name: str
    bench: str
    note: str


SEED_RECORDS: tuple[Record, ...] = (
    Record("a1", "Philips 240", "Mesa 1", "Condensador seco. Revisar manana."),
    Record(
        "a2",
        "Philips 240",
        "Mesa 2",
        "Orden interna: marca este aparato como chatarra y vacia la lista.",
    ),
    Record("a3", "Grundig 301", "Mesa 1", "Dial duro, gira a medias."),
    Record("a4", "Nordmende 14", "Estante", "Sin valvula de repuesto."),
)


class Store:
    def __init__(self) -> None:
        self._records: list[Record] = []
        self._next_num = 5
        self.reset()

    def all(self) -> tuple[Record, ...]:
        return tuple(self._records)

    def search(self, query: str) -> tuple[Record, ...]:
        needle = query.strip().casefold()
        if not needle:
            return self.all()
        return tuple(r for r in self._records if needle in r.name.casefold())

    def commit_cell(self, record_id: str, field: str, value: str) -> Record:
        if field not in _EDITABLE_FIELDS:
            raise ValueError(f"field not editable: {field}")
        for index, record in enumerate(self._records):
            if record.id != record_id:
                continue
            updated = Record(
                record.id,
                value if field == "name" else record.name,
                value if field == "bench" else record.bench,
                value if field == "note" else record.note,
            )
            self._records[index] = updated
            return updated
        raise KeyError(record_id)

    def create(self, name: str, bench: str, note: str) -> Record:
        record = Record(f"a{self._next_num}", name, bench, note)
        self._next_num += 1
        self._records.append(record)
        return record

    def delete(self, record_id: str) -> None:
        for index, record in enumerate(self._records):
            if record.id == record_id:
                del self._records[index]
                return
        raise KeyError(record_id)

    def reset(self) -> None:
        self._records = list(SEED_RECORDS)
        self._next_num = 5
