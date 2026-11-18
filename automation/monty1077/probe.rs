use std::{env, fs};

use monty::MontyRun;

fn main() {
    let path = env::args().nth(1).expect("expression file");
    let expressions = fs::read_to_string(path).expect("read expressions");
    for (index, expression) in expressions.lines().enumerate() {
        let result = MontyRun::new(
            expression.to_owned(),
            "round_probe.py",
            vec![],
            Default::default(),
        )
        .and_then(|mut run| run.run_no_limits(vec![]));
        match result {
            Ok(value) => println!("{index}\tOK\t{value:?}"),
            Err(error) => println!("{index}\tERROR\t{}", error.to_string().escape_default()),
        }
    }
}
