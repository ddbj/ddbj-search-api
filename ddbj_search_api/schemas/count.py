from pydantic import BaseModel, ConfigDict, Field


class TypeCounts(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    bioproject: int = Field(..., ge=0)
    biosample: int = Field(..., ge=0)
    sra_submission: int = Field(..., ge=0, alias="sra-submission")
    sra_study: int = Field(..., ge=0, alias="sra-study")
    sra_experiment: int = Field(..., ge=0, alias="sra-experiment")
    sra_run: int = Field(..., ge=0, alias="sra-run")
    sra_sample: int = Field(..., ge=0, alias="sra-sample")
    sra_analysis: int = Field(..., ge=0, alias="sra-analysis")
    jga_study: int = Field(..., ge=0, alias="jga-study")
    jga_dataset: int = Field(..., ge=0, alias="jga-dataset")
    jga_dac: int = Field(..., ge=0, alias="jga-dac")
    jga_policy: int = Field(..., ge=0, alias="jga-policy")
