import math
import unittest

from evidence_workflow.meta import normalized_resolution, random_effects_meta, zone_probabilities
from evidence_workflow.models import AnalysisConfig, Study
from evidence_workflow.pipeline import classify_numeric, process_study, run_pipeline


CFG = AnalysisConfig(sesoi_low=-0.2, sesoi_high=0.2)


class WorkflowTests(unittest.TestCase):
    def test_tost_uses_90_percent_interval_at_alpha_005(self):
        s = process_study(Study("x", "a", effect=0.0, se=0.1), CFG)
        # 90% CI is approximately [-0.1645, 0.1645], wholly inside the SESOI.
        self.assertEqual(classify_numeric(s, CFG), "clinically_equivalent")


    def test_censored_p_is_not_reconstructed(self):
        s = Study("x", "a", p_value=0.05, p_operator="<", test_type="two_sample_t",
                  direction=1, n1=50, n2=50, qualitative_label="unclear")
        result = process_study(s, CFG)
        self.assertEqual(result.tier, "qualitative")
        self.assertIsNone(result.effect)
        self.assertIn("Censored p-values", result.notes[0])


    def test_exact_p_reconstruction_requires_test_metadata(self):
        s = Study("x", "a", p_value=0.03, direction=1, n1=50, n2=50)
        result = process_study(s, CFG)
        self.assertEqual(result.tier, "unextractable")
        self.assertIsNone(result.effect)


    def test_exact_two_sample_p_reconstructs_low_confidence_effect(self):
        s = Study("x", "a", p_value=0.05, test_type="two_sample_t", direction=1, n1=50, n2=50)
        result = process_study(s, CFG)
        self.assertEqual(result.tier, "p_reconstructed")
        self.assertTrue(result.reconstructed)
        self.assertGreater(result.effect, 0)
        self.assertGreater(result.variance, 0)


    def test_three_way_resolution_penalizes_opposite_tails(self):
        split = {"harm": 0.5, "clinically_negligible": 0.0, "benefit": 0.5}
        decisive = {"harm": 0.0, "clinically_negligible": 0.99, "benefit": 0.01}
        self.assertLess(normalized_resolution(split), normalized_resolution(decisive))


    def test_random_effects_detects_conflict(self):
        meta = random_effects_meta([-0.8, 0.8, -0.7, 0.7], [0.01] * 4)
        self.assertGreater(meta.i2, 0.9)
        self.assertGreater(meta.tau2, 0)


    def test_pipeline_separates_primary_and_reconstructed_sensitivity(self):
        studies = [
            Study("s1", "a", outcome="y", timepoint="12w", measure="hedges_g", scale_id="v1", effect=0.0, se=0.08),
            Study("s2", "a", outcome="y", timepoint="12w", measure="hedges_g", scale_id="v1", effect=0.03, se=0.09),
            Study("s3", "a", outcome="y", timepoint="12w", measure="hedges_g", scale_id="v1", p_value=0.2,
                  test_type="two_sample_t", direction=1, n1=60, n2=60),
        ]
        report = run_pipeline(studies, CFG)
        self.assertEqual(report["clusters_primary"][0]["n_numeric_eligible"], 2)
        self.assertEqual(report["clusters_with_p_reconstruction"][0]["n_numeric_eligible"], 3)


    def test_zone_probabilities_sum_to_one(self):
        p = zone_probabilities(0.0, 0.1, -0.2, 0.2)
        self.assertTrue(math.isclose(sum(p.values()), 1.0))

    def test_lower_is_better_is_oriented_to_positive_benefit(self):
        s = process_study(Study("x", "a", effect=-0.6, se=0.1, higher_is_better=False), CFG)
        self.assertEqual(s.analysis_effect, 0.6)
        self.assertEqual(s.finding, "benefit")


if __name__ == "__main__":
    unittest.main()
