"""Organizational US band hints; never transmitted identity evidence."""
FAMILIES={
    'amateur':[(3500000,4000000),(7000000,7300000),(14000000,14350000),(28000000,29700000),(50000000,54000000),(144000000,148000000),(222000000,225000000),(420000000,450000000)],
    'marine':[(156000000,162050000)],
    'aircraft':[(118000000,137000000),(977500000,978500000),(1089000000,1091000000)],
    'weather':[(162400000,162550000),(400000000,406000000)],
    'sensors':[(314500000,315500000),(433050000,434790000),(902000000,928000000)],
    'broadcast':[(520000,1710000),(87500000,108000000)],
}


def contexts(frequency):
    return [name for name,intervals in FAMILIES.items() if any(low<=frequency<=high for low,high in intervals)]
