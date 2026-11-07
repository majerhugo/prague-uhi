# Nainštaluje R balíky potrebné pre kontrolu kvality (qc.py / qc.R).
# Beží lokálne aj vo workflowe. Hotové balíky preskočí, takže opakované spustenie je lacné.
CROWDQC_REF <- "6aa41e04f56e45db055256f95ecfe38e2b1abcf9"   # CrowdQCplus v1.1.2 (tag v1.1.2)

# Repozitár z prostredia (vo workflowe binárny RSPM z setup-r); bez neho cloud.r-project.org
repos <- getOption("repos")
if (is.null(repos) || identical(unname(repos["CRAN"]), "@CRAN@")) repos <- c(CRAN = "https://cloud.r-project.org")
cran <- c("data.table", "robustbase", "lubridate", "terra", "geodata")
missing <- cran[!vapply(cran, requireNamespace, logical(1), quietly = TRUE)]
if (length(missing)) install.packages(missing, repos = repos)

if (!requireNamespace("CrowdQCplus", quietly = TRUE)) {
  # Priamo tarball z github.com (nie cez api.github.com, ktoré na zdieľaných adresách runnerov limituje
  # požiadavky); závislosti sú už nainštalované vyššie, preto repos = NULL.
  install.packages(paste0("https://github.com/dafenner/CrowdQCplus/archive/", CROWDQC_REF, ".tar.gz"),
                   repos = NULL, type = "source")
}
library(CrowdQCplus)   # zlyhá, ak sa balík nepodarilo nainštalovať alebo načítať
cat("CrowdQCplus", as.character(packageVersion("CrowdQCplus")), "\n")
