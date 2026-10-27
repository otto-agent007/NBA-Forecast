"""Small standard-library statistics for the research scripts: OLS with cluster-robust errors, no numpy."""
from collections import defaultdict
import math
from statistics import fmean, pstdev


def solve(matrix, vector):
    """Gaussian elimination with partial pivoting for a small square system."""
    n = len(vector)
    rows = [list(row) + [value] for row, value in zip(matrix, vector)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(rows[r][col]))
        rows[col], rows[pivot] = rows[pivot], rows[col]
        if abs(rows[col][col]) < 1e-12:
            raise ValueError('singular design')
        for r in range(n):
            if r != col:
                factor = rows[r][col] / rows[col][col]
                rows[r] = [a - factor * b for a, b in zip(rows[r], rows[col])]
    return [rows[i][n] / rows[i][i] for i in range(n)]


def invert(matrix):
    n = len(matrix)
    columns = [solve(matrix, [1. if i == j else 0. for i in range(n)]) for j in range(n)]
    return [[columns[j][i] for j in range(n)] for i in range(n)]


def ols(design, y, clusters=None):
    """Coefficients and standard errors of y on the design rows (include a 1 for the intercept).

    With ``clusters`` (one label per row) the errors are cluster-robust with the
    G/(G-1) small-sample factor; otherwise they are the usual OLS errors.
    """
    n, k = len(y), len(design[0])
    xtx = [[sum(r[i] * r[j] for r in design) for j in range(k)] for i in range(k)]
    xty = [sum(r[i] * v for r, v in zip(design, y)) for i in range(k)]
    beta = solve(xtx, xty)
    residuals = [v - sum(b * x for b, x in zip(beta, r)) for r, v in zip(design, y)]
    inverse = invert(xtx)
    if clusters is None:
        sigma2 = sum(e * e for e in residuals) / (n - k)
        return beta, [math.sqrt(sigma2 * inverse[i][i]) for i in range(k)]
    sums = defaultdict(lambda: [0.] * k)
    for r, e, label in zip(design, residuals, clusters):
        for i in range(k):
            sums[label][i] += r[i] * e
    groups = len(sums)
    meat = [[sum(s[i] * s[j] for s in sums.values()) for j in range(k)] for i in range(k)]
    middle = [[sum(inverse[i][a] * meat[a][b] for a in range(k)) for b in range(k)] for i in range(k)]
    variance = [[sum(middle[i][a] * inverse[a][j] for a in range(k)) * groups / (groups - 1) for j in range(k)] for i in range(k)]
    return beta, [math.sqrt(max(variance[i][i], 0.)) for i in range(k)]


def slope(x, y, clusters=None):
    """'+0.49 ± 0.06': the slope of y on x with an intercept, and its (clustered) standard error."""
    beta, se = ols([[1., v] for v in x], y, clusters)
    return beta[1], se[1]


def corr(x, y):
    mx, my = fmean(x), fmean(y)
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    return sxy / math.sqrt(sum((a - mx) ** 2 for a in x) * sum((b - my) ** 2 for b in y))


def sd(values):
    return pstdev(values)
