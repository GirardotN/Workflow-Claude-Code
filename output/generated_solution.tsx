```python
# Module généré automatiquement par l'agent de développement
from typing import Any, Dict

def process_task(data: Dict[str, Any]) -> Dict[str, Any]:
    """Traite une tâche avec validation de contrat."""
    if not isinstance(data, dict):
        raise ValueError('Données invalides : dictionnaire attendu')
    return {'status': 'success', 'processed': True, 'payload': data}
```