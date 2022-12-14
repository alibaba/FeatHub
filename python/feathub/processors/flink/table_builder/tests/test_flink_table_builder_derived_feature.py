#  Copyright 2022 The Feathub Authors
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      https://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.

from datetime import timedelta

import pandas as pd

from feathub.common.exceptions import FeathubException
from feathub.common.types import Float64, Int64, String
from feathub.feature_tables.sources.datagen_source import DataGenSource
from feathub.feature_views.derived_feature_view import DerivedFeatureView
from feathub.feature_views.feature import Feature
from feathub.feature_views.transforms.over_window_transform import OverWindowTransform
from feathub.feature_views.transforms.python_udf_transform import PythonUdfTransform
from feathub.processors.flink.table_builder.tests.table_builder_test_utils import (
    FlinkTableBuilderTestBase,
)
from feathub.table.schema import Schema


class FlinkTableBuilderDerivedFeatureViewTest(FlinkTableBuilderTestBase):
    def test_derived_feature_view(self):
        df = self.input_data.copy()
        source = self._create_file_source(df)

        f_cost_per_mile = Feature(
            name="cost_per_mile",
            dtype=Float64,
            transform="CAST(cost AS DOUBLE) / CAST(distance AS DOUBLE) + 10",
        )

        f_total_cost = Feature(
            name="total_cost",
            dtype=Int64,
            transform=OverWindowTransform(
                expr="cost",
                agg_func="SUM",
                group_by_keys=["name"],
                window_size=timedelta(days=2),
            ),
        )
        f_avg_cost = Feature(
            name="avg_cost",
            dtype=Float64,
            transform=OverWindowTransform(
                expr="cost",
                agg_func="AVG",
                group_by_keys=["name"],
                window_size=timedelta(days=2),
            ),
        )
        f_max_cost = Feature(
            name="max_cost",
            dtype=Int64,
            transform=OverWindowTransform(
                expr="cost",
                agg_func="MAX",
                group_by_keys=["name"],
                window_size=timedelta(days=2),
            ),
        )
        f_min_cost = Feature(
            name="min_cost",
            dtype=Int64,
            transform=OverWindowTransform(
                expr="cost",
                agg_func="MIN",
                group_by_keys=["name"],
                window_size=timedelta(days=2),
            ),
        )

        features = DerivedFeatureView(
            name="feature_view",
            source=source,
            features=[
                f_cost_per_mile,
                f_total_cost,
                f_avg_cost,
                f_max_cost,
                f_min_cost,
            ],
            keep_source_fields=False,
        )

        result_df = (
            self.flink_table_builder.build(features=features)
            .to_pandas()
            .sort_values(by=["name", "time"])
            .reset_index(drop=True)
        )

        expected_result_df = df
        expected_result_df["cost_per_mile"] = expected_result_df.apply(
            lambda row: row["cost"] / row["distance"] + 10, axis=1
        )
        expected_result_df["total_cost"] = pd.Series([100, 400, 400, 600, 500, 900])
        expected_result_df["avg_cost"] = pd.Series(
            [100.0, 400.0, 200.0, 300.0, 500.0, 450.0]
        )
        expected_result_df["max_cost"] = pd.Series([100, 400, 300, 400, 500, 600])
        expected_result_df["min_cost"] = pd.Series([100, 400, 100, 200, 500, 300])
        expected_result_df.drop(["cost", "distance"], axis=1, inplace=True)
        expected_result_df = expected_result_df.sort_values(
            by=["name", "time"]
        ).reset_index(drop=True)

        self.assertIsNone(source.keys)
        self.assertListEqual(["name"], features.keys)
        self.assertTrue(expected_result_df.equals(result_df))

    def test_join_transform(self):
        df_1 = self.input_data.copy()
        source = self._create_file_source(df_1)
        feature_view_1 = DerivedFeatureView(
            name="feature_view_1",
            source=source,
            features=[
                Feature(
                    name="cost",
                    dtype=Int64,
                    transform="cost",
                ),
                Feature(
                    name="distance",
                    dtype=Int64,
                    transform="distance",
                ),
            ],
            keep_source_fields=True,
        )

        df_2 = pd.DataFrame(
            [
                ["Alex", 100.0, "2022-01-01,09:01:00"],
                ["Emma", 400.0, "2022-01-01,09:02:00"],
                ["Alex", 200.0, "2022-01-02,09:03:00"],
                ["Emma", 300.0, "2022-01-02,09:04:00"],
                ["Jack", 500.0, "2022-01-03,09:05:00"],
                ["Alex", 450.0, "2022-01-03,09:06:00"],
            ],
            columns=["name", "avg_cost", "time"],
        )
        source_2 = self._create_file_source(
            df_2,
            schema=Schema(["name", "avg_cost", "time"], [String, Float64, String]),
            timestamp_format="%Y-%m-%d,%H:%M:%S",
            keys=["name"],
        )
        feature_view_2 = DerivedFeatureView(
            name="feature_view_2",
            source=feature_view_1,
            features=[
                Feature(
                    name="cost",
                    dtype=Int64,
                    transform="cost",
                ),
                "distance",
                f"{source_2.name}.avg_cost",
            ],
            keep_source_fields=False,
        )

        feature_view_3 = DerivedFeatureView(
            name="feature_view_3",
            source=feature_view_2,
            features=[
                Feature(
                    name="derived_cost",
                    dtype=Float64,
                    transform="avg_cost * distance",
                ),
            ],
            keep_source_fields=True,
        )

        [_, built_feature_view_2, built_feature_view_3] = self.registry.build_features(
            [source_2, feature_view_2, feature_view_3]
        )

        expected_result_df = df_1
        expected_result_df["avg_cost"] = pd.Series(
            [None, None, 100.0, 400.0, None, 200.0]
        )
        expected_result_df["derived_cost"] = pd.Series(
            [None, None, 20000.0, 100000.0, None, 160000.0]
        )
        expected_result_df = expected_result_df.sort_values(
            by=["name", "time"]
        ).reset_index(drop=True)

        result_df = (
            self.flink_table_builder.build(features=built_feature_view_3)
            .to_pandas()
            .sort_values(by=["name", "time"])
            .reset_index(drop=True)
        )

        self.assertIsNone(feature_view_1.keys)
        self.assertListEqual(["name"], built_feature_view_2.keys)
        self.assertListEqual(["name"], built_feature_view_3.keys)
        self.assertTrue(expected_result_df.equals(result_df))

    def test_expression_transform_on_joined_field(self):
        df_1 = self.input_data.copy()
        source = self._create_file_source(df_1)

        df_2 = pd.DataFrame(
            [
                ["Alex", 100.0, "2022-01-01,09:01:00"],
                ["Emma", 400.0, "2022-01-01,09:02:00"],
                ["Alex", 200.0, "2022-01-02,09:03:00"],
                ["Emma", 300.0, "2022-01-02,09:04:00"],
                ["Jack", 500.0, "2022-01-03,09:05:00"],
                ["Alex", 450.0, "2022-01-03,09:06:00"],
            ],
            columns=["name", "avg_cost", "time"],
        )
        source_2 = self._create_file_source(
            df_2,
            schema=Schema(["name", "avg_cost", "time"], [String, Float64, String]),
            timestamp_format="%Y-%m-%d,%H:%M:%S",
            keys=["name"],
        )
        feature_view_2 = DerivedFeatureView(
            name="feature_view_2",
            source=source,
            features=[
                Feature(
                    name="cost",
                    dtype=Int64,
                    transform="cost",
                ),
                "distance",
                f"{source_2.name}.avg_cost",
                Feature(
                    name="derived_cost",
                    dtype=Float64,
                    transform="avg_cost * distance",
                ),
            ],
            keep_source_fields=False,
        )

        [_, built_feature_view_2] = self.registry.build_features(
            [source_2, feature_view_2]
        )

        expected_result_df = df_1
        expected_result_df["avg_cost"] = pd.Series(
            [None, None, 100.0, 400.0, None, 200.0]
        )
        expected_result_df["derived_cost"] = pd.Series(
            [None, None, 20000.0, 100000.0, None, 160000.0]
        )
        expected_result_df = expected_result_df.sort_values(
            by=["name", "time"]
        ).reset_index(drop=True)

        result_df = (
            self.flink_table_builder.build(features=built_feature_view_2)
            .to_pandas()
            .sort_values(by=["name", "time"])
            .reset_index(drop=True)
        )

        self.assertListEqual(["name"], built_feature_view_2.keys)
        self.assertTrue(expected_result_df.equals(result_df))

    def test_over_window_on_join_field(self):
        df_1 = self.input_data.copy()
        source = self._create_file_source(df_1)

        df_2 = pd.DataFrame(
            [
                ["Alex", 100.0, "2022-01-01,09:01:00"],
                ["Emma", 400.0, "2022-01-01,09:02:00"],
                ["Alex", 200.0, "2022-01-02,09:03:00"],
                ["Emma", 300.0, "2022-01-02,09:04:00"],
                ["Jack", 500.0, "2022-01-03,09:05:00"],
                ["Alex", 450.0, "2022-01-03,09:06:00"],
            ],
            columns=["name", "avg_cost", "time"],
        )
        source_2 = self._create_file_source(
            df_2,
            schema=Schema(["name", "avg_cost", "time"], [String, Float64, String]),
            timestamp_format="%Y-%m-%d,%H:%M:%S",
            keys=["name"],
        )
        feature_view_2 = DerivedFeatureView(
            name="feature_view_2",
            source=source,
            features=[
                Feature(
                    name="cost",
                    dtype=Int64,
                    transform="cost",
                ),
                "distance",
                f"{source_2.name}.avg_cost",
                Feature(
                    name="derived_cost",
                    dtype=Float64,
                    transform="avg_cost * distance",
                ),
                Feature(
                    name="last_avg_cost",
                    dtype=Int64,
                    transform=OverWindowTransform(
                        expr="avg_cost",
                        agg_func="LAST_VALUE",
                        window_size=timedelta(days=2),
                        group_by_keys=["name"],
                        limit=2,
                    ),
                ),
                Feature(
                    name="double_last_avg_cost",
                    dtype=Float64,
                    transform="last_avg_cost * 2",
                ),
            ],
            keep_source_fields=False,
        )

        [_, built_feature_view_2] = self.registry.build_features(
            [source_2, feature_view_2]
        )

        expected_result_df = df_1
        expected_result_df["avg_cost"] = pd.Series(
            [None, None, 100.0, 400.0, None, 200.0]
        )
        expected_result_df["derived_cost"] = pd.Series(
            [None, None, 20000.0, 100000.0, None, 160000.0]
        )
        expected_result_df["last_avg_cost"] = pd.Series(
            [None, None, 100.0, 400.0, None, 200.0]
        )
        expected_result_df["double_last_avg_cost"] = pd.Series(
            [None, None, 200.0, 800.0, None, 400.0]
        )
        expected_result_df = expected_result_df.sort_values(
            by=["name", "time"]
        ).reset_index(drop=True)

        result_df = (
            self.flink_table_builder.build(features=built_feature_view_2)
            .to_pandas()
            .sort_values(by=["name", "time"])
            .reset_index(drop=True)
        )

        self.assertListEqual(["name"], built_feature_view_2.keys)
        self.assertTrue(expected_result_df.equals(result_df))

    def test_python_udf_transform(self):
        df_1 = self.input_data.copy()
        source = self._create_file_source(df_1)

        def name_to_lower(row: pd.Series) -> str:
            return row["name"].lower()

        feature_view = DerivedFeatureView(
            name="feature_view",
            source=source,
            features=[
                Feature(
                    name="lower_name",
                    dtype=String,
                    transform=PythonUdfTransform(name_to_lower),
                    keys=["name"],
                )
            ],
        )

        expected_result_df = df_1
        expected_result_df["lower_name"] = expected_result_df["name"].apply(
            lambda name: name.lower()
        )
        expected_result_df.drop(["cost", "distance"], axis=1, inplace=True)
        expected_result_df = expected_result_df.sort_values(
            by=["name", "time"]
        ).reset_index(drop=True)

        table = self.flink_table_builder.build(features=feature_view)
        result_df = (
            table.to_pandas().sort_values(by=["name", "time"]).reset_index(drop=True)
        )

        self.assertTrue(expected_result_df.equals(result_df))

    def test_join_transform_with_zoned_timestamp(self):
        prev_local_timezone = (
            self.flink_table_builder.t_env.get_config().get_local_timezone()
        )
        self.flink_table_builder.t_env.get_config().set_local_timezone("Asia/Shanghai")
        df_1 = pd.DataFrame(
            [
                ["Alex", 100, 100, "2022-01-01 08:00:00.000"],
                ["Emma", 400, 250, "2022-01-01 08:00:00.002"],
                ["Alex", 300, 200, "2022-01-01 08:00:00.004"],
                ["Emma", 200, 250, "2022-01-01 08:00:00.006"],
                ["Jack", 500, 500, "2022-01-01 08:00:00.008"],
                ["Alex", 600, 800, "2022-01-01 08:00:00.010"],
            ],
            columns=["name", "cost", "distance", "time"],
        )
        source = self._create_file_source(df_1, timestamp_format="%Y-%m-%d %H:%M:%S.%f")
        feature_view_1 = DerivedFeatureView(
            name="feature_view_1",
            source=source,
            features=[
                Feature(
                    name="cost",
                    dtype=Int64,
                    transform="cost",
                ),
                Feature(
                    name="distance",
                    dtype=Int64,
                    transform="distance",
                ),
            ],
            keep_source_fields=True,
        )

        df_2 = pd.DataFrame(
            [
                ["Alex", 100.0, "2022-01-01 08:00:00.001 +0800"],
                ["Emma", 400.0, "2022-01-01 00:00:00.003 +0000"],
                ["Alex", 200.0, "2022-01-01 08:00:00.005 +0800"],
                ["Emma", 300.0, "2022-01-01 00:00:00.007 +0000"],
                ["Jack", 500.0, "2022-01-01 08:00:00.009 +0800"],
                ["Alex", 450.0, "2022-01-01 00:00:00.011 +0000"],
            ],
            columns=["name", "avg_cost", "time"],
        )
        source_2 = self._create_file_source(
            df_2,
            schema=Schema(["name", "avg_cost", "time"], [String, Float64, String]),
            timestamp_format="%Y-%m-%d %H:%M:%S.%f %z",
            keys=["name"],
        )

        feature_view_2 = DerivedFeatureView(
            name="feature_view_2",
            source=feature_view_1,
            features=[
                Feature(
                    name="cost",
                    dtype=Int64,
                    transform="cost",
                ),
                "distance",
                f"{source_2.name}.avg_cost",
            ],
            keep_source_fields=False,
        )

        feature_view_3 = DerivedFeatureView(
            name="feature_view_3",
            source=feature_view_2,
            features=[
                Feature(
                    name="derived_cost",
                    dtype=Float64,
                    transform="avg_cost * distance",
                ),
            ],
            keep_source_fields=True,
        )

        [_, built_feature_view_2, built_feature_view_3] = self.registry.build_features(
            [source_2, feature_view_2, feature_view_3]
        )

        expected_result_df = df_1
        expected_result_df["avg_cost"] = pd.Series(
            [None, None, 100.0, 400.0, None, 200.0]
        )
        expected_result_df["derived_cost"] = pd.Series(
            [None, None, 20000.0, 100000.0, None, 160000.0]
        )
        expected_result_df = expected_result_df.sort_values(
            by=["name", "time"]
        ).reset_index(drop=True)

        result_df = (
            self.flink_table_builder.build(features=built_feature_view_3)
            .to_pandas()
            .sort_values(by=["name", "time"])
            .reset_index(drop=True)
        )

        self.assertIsNone(feature_view_1.keys)
        self.assertListEqual(["name"], built_feature_view_2.keys)
        self.assertListEqual(["name"], built_feature_view_3.keys)
        self.assertTrue(expected_result_df.equals(result_df))

        self.flink_table_builder.t_env.get_config().set_local_timezone(
            prev_local_timezone
        )

    def test_bounded_left_table_join_unbounded_right_table(self):
        source = DataGenSource(
            name="source_1",
            schema=Schema(["id", "val1", "time"], [Int64, Int64, String]),
            timestamp_field="time",
            timestamp_format="%Y-%m-%d %H:%M:%S",
            keys=["id"],
            number_of_rows=1,
        )

        source_2 = DataGenSource(
            name="source_2",
            schema=Schema(["id", "val2", "time"], [Int64, Int64, String]),
            timestamp_field="time",
            timestamp_format="%Y-%m-%d %H:%M:%S",
            keys=["id"],
        )

        feature_view_1 = DerivedFeatureView(
            name="feature_view_1",
            source=source,
            features=["source_2.val2"],
            keep_source_fields=True,
        )

        built_feature_view = self.registry.build_features([source_2, feature_view_1])[1]

        with self.assertRaises(FeathubException) as cm:
            self.flink_table_builder.build(built_feature_view)

        self.assertIn(
            "Joining a bounded left table with an unbounded right table is currently "
            "not supported.",
            cm.exception.args[0],
        )
