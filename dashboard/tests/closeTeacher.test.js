import test from 'node:test';
import assert from 'node:assert/strict';
import { coefficient, estimate, fitStatus, percent } from '../src/closeTeacher.js';

test('diagnostics preserve signs and unavailable uncertainty', () => {
  assert.equal(estimate({estimate: -1.17, se: .18}), '-1.170 ± 0.180');
  assert.equal(estimate({estimate: 0, se: 0}), '+0.000 ± 0.000');
  assert.equal(estimate({estimate: 1, se: null}), '—');
  assert.equal(percent(null), '—');
  assert.equal(percent(.485), '48.5%');
  assert.equal(coefficient({status: 'singular_design', coefficients: [{feature: 'x', estimate: 0}]}, 'x'), null);
  assert.match(fitStatus({status: 'singular_design'}), /cannot be estimated/);
});
