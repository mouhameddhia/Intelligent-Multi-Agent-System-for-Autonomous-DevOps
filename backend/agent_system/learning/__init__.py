"""Offline learning layer: records closed incidents, evaluates them, distills
lessons into a versioned advisory policy. It never changes model weights and
must never block or fail an investigation."""
