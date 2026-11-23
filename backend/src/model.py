import hashlib
import json
from functools import lru_cache

import numpy as np
import sklearn
from sklearn.decomposition import PCA
from sklearn.metrics import accuracy_score, confusion_matrix
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import LinearSVC

from .schemas import CHANNELS

SEED = 1065
PARAMETERS = {'components': 3, 'svd_solver': 'full', 'C': 0.75, 'dual': False, 'max_iter': 4000}
PROFILES = np.array([
    [0.32, 0.19, 0.83, 0.14, 1.9], [1.9, 0.78, 2.8, 0.42, 5.3], [5.0, 1.7, 5.3, 0.92, 11.0],
])
SPREAD = np.array([0.23, 0.15, 0.48, 0.095, 0.85])


def input_digest(measure):
    payload = {'sample': measure['sample'], 'machine': measure['machine'],
               'revision': measure['revision'], 'channels': {key: measure[key] for key in CHANNELS}}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def reference_splits():
    rng = np.random.default_rng(SEED)

    def draw(samples):
        variation = rng.normal(0, 0.34, size=(3, samples, 1))
        noise = rng.normal(0, 1, size=(3, samples, len(CHANNELS)))
        readings = (PROFILES[:, None, :] + (variation + noise) * SPREAD).clip(0)
        return readings.reshape(-1, len(CHANNELS)), np.repeat(['A', 'B', 'C'], samples)

    return draw(72), draw(24)


@lru_cache(maxsize=1)
def fitted():
    (x_train, y_train), (x_test, y_test) = reference_splits()
    pipeline = Pipeline([
        ('scale', StandardScaler()),
        ('pca', PCA(n_components=PARAMETERS['components'], svd_solver=PARAMETERS['svd_solver'])),
        ('svc', LinearSVC(C=PARAMETERS['C'], dual=PARAMETERS['dual'], max_iter=PARAMETERS['max_iter'], random_state=SEED)),
    ]).fit(x_train, y_train)
    prediction = pipeline.predict(x_test)
    labels = pipeline.classes_.tolist()
    manifest = {
        'version': 'pouch-visual-v1', 'sklearn': sklearn.__version__, 'seed': SEED,
        'parameters': PARAMETERS, 'channels': CHANNELS,
        'reference_sha256': hashlib.sha256(x_train.astype('<f8').tobytes() + ''.join(y_train).encode()).hexdigest(),
        'train_rows': len(x_train), 'holdout_rows': len(x_test),
        'holdout_accuracy': float(accuracy_score(y_test, prediction)),
        'holdout_confusion': confusion_matrix(y_test, prediction, labels=labels).tolist(),
        'variance_ratio': pipeline['pca'].explained_variance_ratio_.tolist(),
    }
    return pipeline, manifest


def predict(measure):
    values = [measure[key] for key in CHANNELS]
    if any(value is None or not np.isfinite(value) for value in values):
        raise ValueError('All five finite readings are required')
    pipeline, manifest = fitted()
    sample = np.asarray([values], dtype='float64')
    grade = str(pipeline.predict(sample)[0])
    evidence = {
        **manifest, 'scores': dict(zip(pipeline.classes_.tolist(), pipeline.decision_function(sample)[0].tolist(), strict=True)),
        'components': pipeline[:-1].transform(sample)[0].tolist(),
    }
    return grade, evidence
