# Nainštaluje R balíky potrebné pre kontrolu kvality (qc.py / qc.R).
# Beží lokálne aj vo workflowe. Hotové balíky preskočí, takže opakované spustenie je lacné.
CROWDQC_REF <- "6aa41e04f56e45db055256f95ecfe38e2b1abcf9"   # CrowdQCplus v1.1.2 (tag v1.1.2)

# Repozitár z prostredia (vo workflowe binárny RSPM z setup-r); bez neho cloud.r-project.org
repos <- getOption("repos")
if (is.null(repos) || identical(unname(repos["CRAN"]), "@CRAN@")) repos <- c(CRAN = "https://cloud.r-project.org")
cran <- c("remotes", "data.table", "robustbase", "lubridate", "terra", "geodata")
missing <- cran[!vapply(cran, requireNamespace, logical(1), quietly = TRUE)]
if (length(missing)) install.packages(missing, repos = repos)

if (!requireNamespace("CrowdQCplus", quietly = TRUE)) {
  remotes::install_github(paste0("dafenner/CrowdQCplus@", CROWDQC_REF), upgrade = "never")
}
library(CrowdQCplus)   # zlyhá, ak sa balík nepodarilo nainštalovať alebo načítať
cat("CrowdQCplus", as.character(packageVersion("CrowdQCplus")), "\n")
